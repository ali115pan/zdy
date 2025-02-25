import os
import json
import socks
import random
import time
import re
import asyncio
import urllib.parse
import logging
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, functions, events
from telethon.tl.types import MessageMediaPhoto, MessageEntityTextUrl
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetHistoryRequest, GetMessagesRequest, DeleteMessagesRequest
from telethon.tl.functions.channels import JoinChannelRequest
from collections import deque
from logging.handlers import TimedRotatingFileHandler

# 设置日志（按天轮换日志文件）
log_handler = TimedRotatingFileHandler(
    'tgzf.log',  # 日志文件
    when='midnight',  # 每天午夜轮换
    backupCount=7  # 保留最近7天的日志
)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# 读取配置文件
config_path = os.path.join(os.path.dirname(__file__), 'tgzf.json')
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

# 从配置文件中获取参数
api_id = config['api_id']
api_hash = config['api_hash']
string_session = config['string_session']
channels_groups_monitor = config['channels_groups_monitor']
forward_to_channel = config['forward_to_channel']
limit = config['limit']
replies_limit = config['replies_limit']
include = config['include']
exclude = config['exclude']
hyperlink_text = config['hyperlink_text']
replacements = config['replacements']
message_md = config['message_md']
channel_match = config['channel_match']
try_join = config['try_join']
check_replies = config['check_replies']
proxy_config = config['proxy']
checknum = config['checknum']
past_years = config['past_years']
only_today = config['only_today']

# 替换 replacements 中的 forward_to_channel 为实际值
if "forward_to_channel" in replacements:
    replacements[forward_to_channel] = replacements.pop("forward_to_channel")

# 设置代理
proxy = None
if proxy_config:
    proxy = (getattr(socks, proxy_config['type']), proxy_config['host'], proxy_config['port'])

class TGForwarder:
    def __init__(self, api_id, api_hash, string_session, channels_groups_monitor, forward_to_channel,
                 limit, replies_limit, include, exclude, check_replies, proxy, checknum, replacements, message_md, channel_match, hyperlink_text, past_years, only_today):
        self.urls_kw = ['magnet', 'drive.uc.cn', 'caiyun.139.com', 'cloud.189.cn', 'pan.quark.cn', '115cdn.com','115.com', 'anxia.com', 'alipan.com', 'aliyundrive.com','pan.baidu.com','mypikpak.com']
        self.checkbox = {"links":[],"sizes":[],"bot_links":{},"chat_forward_count_msg_id":{},"today":"","today_count":0}
        self.checknum = checknum
        self.today_count = 0
        self.history = 'history.json'
        self.pattern = r"(?:链接：\s*)?((?!https?://t\.me)(?:https?://[^\s'】\n]+|magnet:\?xt=urn:btih:[a-zA-Z0-9]+))"
        self.api_id = api_id
        self.api_hash = api_hash
        self.string_session = string_session
        self.channels_groups_monitor = channels_groups_monitor
        self.forward_to_channel = forward_to_channel
        self.limit = limit
        self.replies_limit = replies_limit
        self.include = include
        self.china_timezone_offset = timedelta(hours=8)
        self.today = (datetime.utcnow() + self.china_timezone_offset).date()
        current_year = datetime.now().year - 10
        if not past_years:
            years_list = [str(year) for year in range(1895, current_year)]
            self.exclude = exclude + years_list
        else:
            self.exclude = exclude
        self.only_today = only_today
        self.hyperlink_text = hyperlink_text
        self.replacements = replacements
        self.message_md = message_md
        self.channel_match = channel_match
        self.check_replies = check_replies
        self.download_folder = 'downloads'
        if not proxy:
            self.client = TelegramClient(StringSession(string_session), api_id, api_hash)
        else:
            self.client = TelegramClient(StringSession(string_session), api_id, api_hash, proxy=proxy)
        self.channel_entities = {}  # 缓存频道实体
        self.total = 0  # 初始化 total 为 0

    async def get_channel_entity(self, channel_name):
        """
        获取频道实体并缓存
        """
        if channel_name not in self.channel_entities:
            try:
                self.channel_entities[channel_name] = await self.client.get_entity(channel_name)
            except Exception as e:
                logger.warning(f"频道 {channel_name} 不存在或无法访问: {e}")
                return None
        return self.channel_entities[channel_name]

    def random_wait(self, min_ms, max_ms):
        min_sec = min_ms / 1000
        max_sec = max_ms / 1000
        wait_time = random.uniform(min_sec, max_sec)
        time.sleep(wait_time)

    def contains(self, s, include):
        return any(k in s for k in include)

    def nocontains(self, s, exclude):
        return not any(k in s for k in exclude)

    def replace_targets(self, message: str):
        if self.replacements:
            for target_word, source_words in self.replacements.items():
                if isinstance(source_words, str):
                    source_words = [source_words]
                for word in source_words:
                    message = message.replace(word, target_word)
        message = message.strip()
        return message

    async def dispatch_channel(self, message, jumpLinks=[]):
        hit = False
        if self.channel_match:
            for rule in self.channel_match:
                if rule.get('include'):
                    if not self.contains(message.message, rule['include']):
                        continue
                if rule.get('exclude'):
                    if not self.nocontains(message.message, rule['exclude']):
                        continue
                await self.send(message, rule['target'], jumpLinks)
                hit = True
            if not hit:
                await self.send(message, self.forward_to_channel, jumpLinks)
        else:
            await self.send(message, self.forward_to_channel, jumpLinks)

    async def send(self, message, target_chat_name, jumpLinks=[]):
        text = message.message
        if jumpLinks and self.hyperlink_text:
            categorized_urls = self.categorize_urls(jumpLinks)
            for category, keywords in self.hyperlink_text.items():
                if categorized_urls.get(category):
                    url = categorized_urls[category][0]
                else:
                    continue
                for keyword in keywords:
                    if keyword in text:
                        text = text.replace(keyword, url)
        if self.nocontains(text, self.urls_kw):
            return
        if message.media and isinstance(message.media, MessageMediaPhoto):
            await self.client.send_message(
                target_chat_name,
                self.replace_targets(text),
                file=message.media
            )
        else:
            await self.client.send_message(target_chat_name, self.replace_targets(text))

    async def get_all_replies(self, chat_name, message):
        offset_id = 0
        all_replies = []
        peer = await self.get_channel_entity(chat_name)
        if peer is None:
            return []
        while True:
            try:
                replies = await self.client(functions.messages.GetRepliesRequest(
                    peer=peer,
                    msg_id=message.id,
                    offset_id=offset_id,
                    offset_date=None,
                    add_offset=0,
                    limit=100,
                    max_id=0,
                    min_id=0,
                    hash=0
                ))
                all_replies.extend(replies.messages)
                if len(replies.messages) < 100:
                    break
                offset_id = replies.messages[-1].id
            except Exception as e:
                logger.error(f"获取评论失败: {e}")
                break
        return all_replies

    async def daily_forwarded_count(self, target_channel):
        china_offset = timedelta(hours=8)
        china_tz = timezone(china_offset)
        now = datetime.now(china_tz)
        start_of_day_china = datetime.combine(now.date(), datetime.min.time())
        start_of_day_china = start_of_day_china.replace(tzinfo=china_tz)
        start_of_day_utc = start_of_day_china.astimezone(timezone.utc)
        result = await self.client(GetHistoryRequest(
            peer=target_channel,
            limit=1,
            offset_date=start_of_day_utc,
            offset_id=0,
            add_offset=0,
            max_id=0,
            min_id=0,
            hash=0
        ))
        first_message_pos = result.offset_id_offset
        today_count = first_message_pos if first_message_pos else 0
        msg = f'**今日共更新【{today_count}】条资源 **\n\n'
        return msg, today_count

    async def del_channel_forward_count_msg(self):
        chat_forward_count_msg_id = self.checkbox.get("chat_forward_count_msg_id")
        if not chat_forward_count_msg_id:
            return

        forward_to_channel_message_id = chat_forward_count_msg_id.get(self.forward_to_channel)
        if forward_to_channel_message_id:
            await self.client.delete_messages(self.forward_to_channel, [forward_to_channel_message_id])

        if self.channel_match:
            for rule in self.channel_match:
                target_channel_msg_id = chat_forward_count_msg_id.get(rule['target'])
                await self.client.delete_messages(rule['target'], [target_channel_msg_id])

    async def send_daily_forwarded_count(self):
        await self.del_channel_forward_count_msg()

        chat_forward_count_msg_id = {}
        msg, tc = await self.daily_forwarded_count(self.forward_to_channel)
        sent_message = await self.client.send_message(self.forward_to_channel, msg, parse_mode='md')
        self.checkbox["today_count"] = tc
        await self.client.pin_message(self.forward_to_channel, sent_message.id)
        await self.client.delete_messages(self.forward_to_channel, [sent_message.id + 1])

        chat_forward_count_msg_id[self.forward_to_channel] = sent_message.id
        if self.channel_match:
            for rule in self.channel_match:
                m, t = await self.daily_forwarded_count(rule['target'])
                sm = await self.client.send_message(rule['target'], m)
                self.checkbox["today_count"] = self.checkbox["today_count"] + t
                chat_forward_count_msg_id[rule['target']] = sm.id
                await self.client.pin_message(rule['target'], sm.id)
                await self.client.delete_messages(rule['target'], [sm.id + 1])
        self.checkbox["chat_forward_count_msg_id"] = chat_forward_count_msg_id

    async def redirect_url(self, message):
        links = []
        if message.entities:
            for entity in message.entities:
                if isinstance(entity, MessageEntityTextUrl):
                    if 'start' in entity.url:
                        url = await self.tgbot(entity.url)
                        if url:
                            links.append(url)
                    elif self.nocontains(entity.url, self.urls_kw):
                        continue
                    else:
                        url = urllib.parse.unquote(entity.url)
                        matches = re.findall(self.pattern, url)
                        if matches:
                            links += matches
            return links

    async def tgbot(self, url):
        link = ''
        try:
            bot_username = url.split('/')[-1].split('?')[0]
            query_string = url.split('?')[1]
            command, parameter = query_string.split('=')
            bot_links = self.checkbox["bot_links"]

            if bot_links.get(parameter):
                link = bot_links.get(parameter)
                return link
            else:
                await self.client.send_message(bot_username, f'/{command} {parameter}')
                await asyncio.sleep(2)
                messages = await self.client.get_messages(bot_username, limit=1)
                message = messages[0].message
                links = re.findall(r'(https?://[^\s]+)', message)
                if links:
                    link = links[0]
                    bot_links[parameter] = link
                    self.checkbox["bot_links"] = bot_links
        except Exception as e:
            logger.error(f'TG_Bot 错误: {e}')
        return link

    async def reverse_async_iter(self, async_iter, limit):
        buffer = deque(maxlen=limit)
        async for message in async_iter:
            buffer.append(message)
        for message in reversed(buffer):
            yield message

    async def delete_messages_in_time_range(self, chat_name, start_time_str, end_time_str):
        china_timezone_offset = timedelta(hours=8)
        china_timezone = timezone(china_timezone_offset)
        start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=china_timezone)
        end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=china_timezone)
        chat = await self.get_channel_entity(chat_name)
        async for message in self.client.iter_messages(chat):
            message_china_time = message.date.astimezone(china_timezone)
            if start_time <= message_china_time <= end_time:
                await message.delete()

    async def clear_main(self, start_time, end_time):
        await self.delete_messages_in_time_range(self.forward_to_channel, start_time, end_time)

    def clear(self):
        start_time = "2025-01-08 23:55"
        end_time = "2025-01-09 08:00"
        with self.client.start():
            self.client.loop.run_until_complete(self.clear_main(start_time, end_time))

    def categorize_urls(self, urls):
        categories = {
            "magnet": ["magnet"],
            "uc": ["drive.uc.cn"],
            "mobile": ["caiyun.139.com"],
            "tianyi": ["cloud.189.cn"],
            "quark": ["pan.quark.cn"],
            "115": ["115cdn.com", "115.com", "anxia.com"],
            "aliyun": ["alipan.com", "aliyundrive.com"],
            "pikpak": ["mypikpak.com"],
            "baidu": ["pan.baidu.com"],
            "others": []
        }
        result = {category: [] for category in categories}
        for url in urls:
            if url.startswith("magnet:"):
                result["magnet"].append(url)
                continue
            parsed_url = urllib.parse.urlparse(url)
            domain = parsed_url.netloc.lower()
            categorized = False
            for category, domains in categories.items():
                if any(pattern in domain for pattern in domains):
                    result[category].append(url)
                    categorized = True
                    break
            if not categorized:
                result["others"].append(url)
        return result

    async def deduplicate_links(self, links=[]):
        """
        删除聊天中重复链接的旧消息，只保留最新的消息
        """
        target_links = set(self.checkbox['links']) if not links else links
        if not target_links:
            return

        chats = [self.forward_to_channel]
        if self.channel_match:
            for rule in self.channel_match:
                chats.append(rule['target'])

        for chat_name in chats:
            try:
                chat = await self.get_channel_entity(chat_name)
                if not chat:
                    continue

                links_exist = set()
                messages_to_delete = []
                async for message in self.client.iter_messages(chat, limit=1000):
                    if message.message:
                        links_in_message = re.findall(self.pattern, message.message)
                        if not links_in_message:
                            continue
                        link = links_in_message[0]
                        if link in target_links:
                            if link in links_exist:
                                messages_to_delete.append(message.id)
                            else:
                                links_exist.add(link)

                if messages_to_delete:
                    logger.info(f"【{chat_name}】删除 {len(messages_to_delete)} 条历史重复消息")
                    for i in range(0, len(messages_to_delete), 100):
                        batch = messages_to_delete[i:i + 100]
                        await self.client(DeleteMessagesRequest(chat, batch))
            except Exception as e:
                logger.error(f"删除重复消息时出错: {e}")

    async def checkhistory(self):
        links = []
        sizes = []
        if os.path.exists(self.history):
            with open(self.history, 'r', encoding='utf-8') as f:
                self.checkbox = json.loads(f.read())
                if self.checkbox.get('today') == datetime.now().strftime("%Y-%m-%d"):
                    links = self.checkbox['links']
                    sizes = self.checkbox['sizes']
                else:
                    self.checkbox['links'] = []
                    self.checkbox['sizes'] = []
                    self.checkbox["bot_links"] = {}
                    self.checkbox["today_count"] = 0
                self.today_count = self.checkbox.get('today_count') if self.checkbox.get('today_count') else self.checknum
        self.checknum = self.checknum if self.today_count < self.checknum else self.today_count
        chat = await self.get_channel_entity(self.forward_to_channel)
        messages = self.client.iter_messages(chat, limit=self.checknum)
        async for message in messages:
            if hasattr(message.document, 'mime_type'):
                sizes.append(message.document.size)
            if message.message:
                matches = re.findall(self.pattern, message.message)
                if matches:
                    links.append(matches[0])
        links = list(set(links))
        sizes = list(set(sizes))
        return links, sizes

    async def copy_and_send_message(self, source_chat, target_chat, message_id, text=''):
        try:
            message = await self.client.get_messages(source_chat, ids=message_id)
            if not message:
                logger.warning("未找到消息")
                return
            await self.client.send_message(
                target_chat,
                text,
                file=message.media
            )
        except Exception as e:
            logger.error(f"操作失败: {e}")

    async def forward_messages(self, chat_name, limit, hlinks, hsizes):
        links = hlinks
        sizes = hsizes
        logger.info(f'当前监控频道【{chat_name}】，本次检测最近【{len(links)}】条历史资源进行去重')
        try:
            if try_join:
                await self.client(JoinChannelRequest(chat_name))
            chat = await self.get_channel_entity(chat_name)
            messages = self.client.iter_messages(chat, limit=limit, reverse=False)
            async for message in self.reverse_async_iter(messages, limit=limit):
                if self.only_today:
                    message_china_time = message.date + self.china_timezone_offset
                    if message_china_time.date() != self.today:
                        continue
                self.random_wait(200, 1000)
                if message.media:
                    if hasattr(message.document, 'mime_type') and self.contains(message.document.mime_type, 'video') and self.nocontains(message.message, self.exclude):
                        size = message.document.size
                        text = message.message
                        if message.message:
                            jumpLinks = await self.redirect_url(message)
                            if jumpLinks and self.hyperlink_text:
                                categorized_urls = self.categorize_urls(jumpLinks)
                                for category, keywords in self.hyperlink_text.items():
                                    if categorized_urls.get(category):
                                        url = categorized_urls[category][0]
                                    else:
                                        continue
                                    for keyword in keywords:
                                        if keyword in text:
                                            text = text.replace(keyword, url)
                        if size not in sizes:
                            await self.copy_and_send_message(chat_name, self.forward_to_channel, message.id, text)
                            sizes.append(size)
                            self.total += 1
                        else:
                            logger.info(f'视频已经存在，size: {size}')
                    elif self.contains(message.message, self.include) and message.message and self.nocontains(message.message, self.exclude):
                        jumpLinks = await self.redirect_url(message)
                        matches = re.findall(self.pattern, message.message) if self.contains(message.message, self.urls_kw) else []
                        if matches or jumpLinks:
                            link = jumpLinks[0] if jumpLinks else matches[0]
                            if link not in links:
                                await self.dispatch_channel(message, jumpLinks)
                                self.total += 1
                                links.append(link)
                            else:
                                logger.info(f'链接已存在，link: {link}')
                    elif self.check_replies and message.message and self.nocontains(message.message, self.exclude):
                        replies = await self.get_all_replies(chat_name, message)
                        replies = replies[-self.replies_limit:]
                        for r in replies:
                            if hasattr(r.document, 'mime_type') and self.contains(r.document.mime_type, 'video') and self.nocontains(r.message, self.exclude):
                                size = r.document.size
                                if size not in sizes:
                                    await self.copy_and_send_message(chat_name, self.forward_to_channel, r.id, r.message)
                                    self.total += 1
                                    sizes.append(size)
                                else:
                                    logger.info(f'视频已经存在，size: {size}')
                            elif self.contains(r.message, self.include) and r.message and self.nocontains(r.message, self.exclude):
                                matches = re.findall(self.pattern, r.message)
                                if matches:
                                    link = matches[0]
                                    if link not in links:
                                        await self.dispatch_channel(message)
                                        self.total += 1
                                        links.append(link)
                                    else:
                                        logger.info(f'链接已存在，link: {link}')
                elif message.message:
                    if self.contains(message.message, self.include) and self.nocontains(message.message, self.exclude):
                        jumpLinks = await self.redirect_url(message)
                        matches = re.findall(self.pattern, message.message) if self.contains(message.message, self.urls_kw) else []
                        if matches or jumpLinks:
                            link = jumpLinks[0] if jumpLinks else matches[0]
                            if link not in links:
                                await self.dispatch_channel(message, jumpLinks)
                                self.total += 1
                                links.append(link)
                            else:
                                logger.info(f'链接已存在，link: {link}')
            logger.info(f"从 {chat_name} 转发资源 成功: {self.total}")
            return list(set(links)), list(set(sizes))
        except Exception as e:
            logger.error(f"从 {chat_name} 转发资源 失败: {e}")

    async def main(self):
        start_time = time.time()
        links, sizes = await self.checkhistory()

        # 过滤掉不存在的频道
        valid_channels = []
        for channel_name in self.channels_groups_monitor:
            if '|' in channel_name:
                channel_name = channel_name.split('|')[0]
            if await self.get_channel_entity(channel_name):
                valid_channels.append(channel_name)
            else:
                logger.info(f"跳过不存在的频道: {channel_name}")

        # 如果没有有效的频道，记录日志并结束
        if not valid_channels:
            logger.warning("所有频道均不存在或无法访问，脚本将正常结束。")
            await self.client.disconnect()
            return

        # 并发处理有效的频道
        tasks = []
        for chat_name in valid_channels:
            limit = self.limit
            if '|' in chat_name:
                limit = int(chat_name.split('|')[1])
                chat_name = chat_name.split('|')[0]
            tasks.append(self.forward_messages(chat_name, limit, links, sizes))

        # 等待所有任务完成
        results = await asyncio.gather(*tasks)
        for result in results:
            if result:
                links, sizes = result

        await self.send_daily_forwarded_count()
        with open(self.history, 'w+', encoding='utf-8') as f:
            self.checkbox['links'] = list(set(links))[-self.checkbox["today_count"]:]
            self.checkbox['sizes'] = list(set(sizes))[-self.checkbox["today_count"]:]
            self.checkbox['today'] = datetime.now().strftime("%Y-%m-%d")
            f.write(json.dumps(self.checkbox))
        await self.deduplicate_links()
        await self.client.disconnect()
        end_time = time.time()
        logger.info(f'总耗时: {end_time - start_time} 秒')

    def run(self):
        with self.client.start():
            self.client.loop.run_until_complete(self.main())

# 初始化并运行 TGForwarder
TGForwarder(api_id, api_hash, string_session, channels_groups_monitor, forward_to_channel, limit, replies_limit,
            include, exclude, check_replies, proxy, checknum, replacements, message_md, channel_match, hyperlink_text, past_years, only_today).run()
