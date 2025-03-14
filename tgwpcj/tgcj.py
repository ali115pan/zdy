# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# ----------------------------------------------------------

import json
import re
import os
import time
import logging
from logging.handlers import RotatingFileHandler
import pypinyin
import configparser
from urllib.parse import unquote
from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
from tenacity import retry, stop_after_attempt, wait_fixed

# ------------------------- 配置部分 -------------------------
# 脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))

# 配置日志
log_dir = os.path.join(script_dir, 'logs')  # 日志文件目录
os.makedirs(log_dir, exist_ok=True)  # 创建日志目录（如果不存在）
log_file = os.path.join(log_dir, 'tgcj.log')  # 日志文件路径

# 设置日志处理器
log_handler = RotatingFileHandler(
    log_file,
    maxBytes=10 * 1024 * 1024,  # 每个日志文件最大 10MB
    backupCount=5,  # 最多保留 5 个日志文件
    encoding='utf-8'
)

# 配置日志格式和级别
logging.basicConfig(
    handlers=[log_handler],
    level=logging.INFO,  # 设置日志级别为 INFO
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 加载配置文件
def load_config():
    """加载 tgcj.ini 配置文件"""
    config = configparser.ConfigParser()
    config.read(os.path.join(script_dir, 'tgcj.ini'), encoding='utf-8')

    # Telegram 配置
    global api_id, api_hash, string_session
    api_id = config.get('Telegram', 'api_id')
    api_hash = config.get('Telegram', 'api_hash')
    string_session = config.get('Telegram', 'string_session')

    # 代理配置
    global proxies
    proxies = [
        {
            'proxy_type': config.get('Proxy', 'proxy_type'),
            'addr': config.get('Proxy', 'addr'),
            'port': config.getint('Proxy', 'port'),
            'username': config.get('Proxy', 'username'),
            'password': config.get('Proxy', 'password'),
            'rdns': config.getboolean('Proxy', 'rdns')
        }
    ]

    # 频道列表
    global channel_usernames
    channel_usernames = [
        name.strip() for name in config.get('Channels', 'channel_usernames').split(',')
    ]

    # TypeName 映射关系
    global type_name_mapping
    type_name_mapping = {}
    for key, value in config.items('TypeNameMapping'):
        type_name_mapping[key] = [domain.strip() for domain in value.split(',')]

# 加载配置
load_config()

# 采集配置
json_file_path = os.path.join(script_dir, 'mac_vod.json')  # 以脚本目录为基准
image_dir = os.path.join(script_dir, "data/upload/vod/tgcj/")  # 图片保存目录

# ------------------------- 工具函数 -------------------------
def chinese_to_pinyin(name):
    """中文转拼音"""
    return ''.join(pypinyin.lazy_pinyin(name, style=pypinyin.Style.NORMAL))

def get_type_name_by_url(text, url):
    """根据 message.text 内容和链接内容确定 type_name"""
    if "国产剧" in text:
        return "国产剧"
    elif "韩日泰" in text:
        return "韩日泰"
    elif "欧美剧" in text:
        return "欧美剧"
    elif "综艺" in text:
        return "综艺"
    elif "国漫" in text or "日漫" in text or "美漫" in text or "漫画" in text:
        return "动漫"  # 新增的动漫类型
    else:
        # 根据 URL 中的域名确定 type_name
        for type_name, domains in type_name_mapping.items():
            if any(domain in url for domain in domains):
                return type_name
        return "网盘"  # 默认值，用于未匹配到的情况

def load_db_mapping():
    """加载 db.ini 文件并解析为字典"""
    db_mapping = {}
    db_file_path = os.path.join(script_dir, 'db.ini')
    if os.path.exists(db_file_path):
        with open(db_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    db_mapping[line] = line  # 键和值相同，用于完整匹配
    return db_mapping

def save_data(table_data):
    """保存数据到文件"""
    with open(json_file_path, 'w', encoding='utf-8') as f:
        json.dump(table_data, f, ensure_ascii=False, indent=4)

def load_unique_keys():
    """加载持久化的唯一键集合"""
    unique_keys_file = os.path.join(script_dir, 'unique_keys.json')
    if os.path.exists(unique_keys_file):
        with open(unique_keys_file, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    return set()

def save_unique_keys(unique_keys):
    """保存唯一键集合到文件"""
    unique_keys_file = os.path.join(script_dir, 'unique_keys.json')
    with open(unique_keys_file, 'w', encoding='utf-8') as f:
        json.dump(list(unique_keys), f, ensure_ascii=False, indent=4)

def load_last_message_ids():
    """加载每个频道的最后一条消息 ID"""
    last_message_ids_file = os.path.join(script_dir, 'last_message_ids.json')
    if os.path.exists(last_message_ids_file):
        with open(last_message_ids_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_last_message_ids(last_message_ids):
    """保存每个频道的最后一条消息 ID"""
    last_message_ids_file = os.path.join(script_dir, 'last_message_ids.json')
    with open(last_message_ids_file, 'w', encoding='utf-8') as f:
        json.dump(last_message_ids, f, ensure_ascii=False, indent=4)

def normalize_link(link):
    """规范化链接，去除多余参数并统一为小写"""
    link = re.sub(r'(\?|&).*', '', link)  # 去除参数
    return link.lower().strip()  # 统一为小写

# ------------------------- 采集模块 -------------------------
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))  # 重试 3 次，每次间隔 2 秒
async def safe_iter_messages(client, channel, limit=50, offset_id=0):
    """安全的消息迭代器，支持增量采集"""
    try:
        async for message in client.iter_messages(channel, limit=limit, offset_id=offset_id):
            yield message
    except Exception as e:
        logging.warning(f"获取消息失败：{str(e)}")  # 使用 WARNING 级别记录错误
        raise e  # 触发重试

async def get_client():
    """获取 Telegram 客户端，支持代理轮换"""
    for proxy in proxies:
        try:
            client = TelegramClient(
                StringSession(string_session),
                api_id,
                api_hash,
                proxy=proxy
            )
            await client.start()
            logging.info(f"成功连接到代理：{proxy['addr']}:{proxy['port']}")
            return client
        except Exception as e:
            logging.warning(f"代理 {proxy['addr']}:{proxy['port']} 不可用：{str(e)}")  # 使用 WARNING 级别记录错误
            continue
    raise Exception("所有代理均不可用")

async def process_message(client, message, table_data, unique_keys, lock):
    """处理单条消息"""
    text = message.text if isinstance(message.text, str) else ''
    if not text.strip() or "@Quark_Movies" in text:
        return None

    # 初始化 vod_name
    vod_name = None

    # 尝试多种正则表达式提取 vod_name
    patterns = [
        r'(?:名称：|电视剧名：|片名：|标题：)([^（(]+)',  # 匹配 "名称：" 或 "电视剧名：" 等
        r'《([^》]+)》',  # 匹配中文书名号
        r'([^\n]+)\n链接：',  # 匹配消息第一行作为名称
    ]

    for pattern in patterns:
        vod_name_match = re.search(pattern, text)
        if vod_name_match:
            vod_name = vod_name_match.group(1).strip().lstrip('*')
            vod_name = re.sub(r'(.*?)\s+.*', r'\1', vod_name).strip()[:15]
            break

    # 如果没有提取到 vod_name，跳过该消息
    if not vod_name:
        logging.warning(f"无法提取资源名称，跳过消息：{text[:50]}...")  # 记录前50个字符
        return None

    # 如果 vod_name 是纯英文或数字，跳过
    if re.fullmatch(r'^[A-Za-z0-9\s\-\'":,.!?()]+$', vod_name):
        logging.warning(f"资源名称为纯英文或数字，跳过：{vod_name}")
        return None

    # 提取链接
    link_match = re.search(
        r"(?:链接：\s*)?((?!https?://t\.me)(?:https?://[^\s'】\n]+|magnet:\?xt=urn:btih:[a-zA-Z0-9]+))",
        text,
        re.IGNORECASE
    )
    if not link_match:
        logging.warning(f"未找到有效链接，跳过消息：{vod_name}")
        return None

    raw_link = link_match.group(1) or link_match.group(2)
    link = unquote(raw_link.strip())
    normalized_link = normalize_link(link)  # 规范化链接

    # 检查是否已存在同名记录
    composite_key = f"{vod_name}_{json.dumps([normalized_link], sort_keys=True)}"
    async with lock:  # 使用锁确保线程安全
        if composite_key in unique_keys:
            logging.info(f"跳过重复消息：{vod_name} (原始链接: {raw_link}, 解码后链接: {link})")
            return None

        # 提取描述
        vod_content = "暂无简介"  # 默认值
        description_match = re.search(r'(?:描述：|剧情简介:)\s*([\s\S]*)', text)
        if description_match:
            vod_content = description_match.group(1).strip()  # 提取描述内容
            vod_content = re.sub(r'\n.*', '', vod_content)  # 去除 \n 及其后面的内容
            vod_content = vod_content[:150]  # 限制描述内容不超过 100 个字符

        # 提取年份信息
        vod_year = ""
        for year in ["2025", "2024", "2023", "2022", "2021", "2020"]:
            if year in text:
                vod_year = year
                break

        # 在 vod_name 上加上 vod_year
        if vod_year:
            vod_name = f"{vod_name} ({vod_year})"

        # 创建新记录
        new_entry = {
            "vod_id": str(len(table_data) + 1),
            "vod_name": vod_name,
            "vod_en": chinese_to_pinyin(vod_name),
            "vod_status": "1",
            "vod_letter": vod_name[0].upper(),
            "vod_time": str(int(message.date.timestamp())),
            "vod_time_add": str(int(message.date.timestamp())),
            "vod_down_url": [link],
            "vod_down_from": "BJ$$$BJ",
            "type_name": get_type_name_by_url(text, link),  # 动态设置 type_name
            "vod_content": vod_content,  # 使用提取的描述
            "vod_pic": "",  # 图片路径先留空
            "vod_year": vod_year  # 新增字段
        }

        # 图片处理（异步下载）
        if message.media and hasattr(message.media, 'photo'):
            try:
                media = message.media.photo
                filename = f"{message.id}_{media.id}_{media.access_hash}.jpg"
                full_path = os.path.join(image_dir, filename)
                relative_path = f"/upload/vod/tgcj/{filename}"

                if not os.path.exists(full_path):
                    await client.download_media(
                        message.media,
                        file=full_path,
                        thumb=-1
                    )
                    logging.info(f"图片下载成功：{filename}")
                new_entry["vod_pic"] = relative_path
            except Exception as e:
                logging.warning(f"图片处理失败：{str(e)}")  # 使用 WARNING 级别记录错误

        # 添加复合键到唯一键集合
        unique_keys.add(composite_key)
        table_data.append(new_entry)
        save_data(table_data)  # 实时保存数据

        return new_entry

async def process_channel(client, channel, table_data, unique_keys, lock, last_message_id):
    """处理单个频道的消息"""
    collected_count = 0  # 定义 collected_count
    latest_message_id = last_message_id  # 初始化 latest_message_id

    try:
        # 获取当前频道的消息总数
        total_messages = await client.get_messages(channel, limit=1)
        logging.info(f"频道 {channel} 的消息总数为：{total_messages.total}")

        # 如果 last_message_id 无效（小于等于 0），则从最新消息开始采集
        if last_message_id <= 0:
            logging.warning(f"频道 {channel} 的 last_message_id 无效（{last_message_id}），从最新消息开始采集")
            last_message_id = 0  # 设置为 0，表示从最新消息开始

        # 采集消息（从新到旧）
        async for message in safe_iter_messages(client, channel, limit=1000, offset_id=last_message_id):
            try:
                new_entry = await process_message(client, message, table_data, unique_keys, lock)  # 传递 client 和 lock
                if new_entry:
                    collected_count += 1
                    # 确保 latest_message_id 是最大的消息 ID
                    if message.id > latest_message_id:
                        latest_message_id = message.id
                    logging.info(f"[消息ID:{message.id}] 采集成功：{new_entry['vod_name']} (年份: {new_entry['vod_year']})")
            except Exception as e:
                logging.warning(f"处理消息时出错：{str(e)}")  # 使用 WARNING 级别记录错误
                continue  # 跳过当前消息，继续处理下一条

    except Exception as e:
        logging.warning(f"采集频道 {channel} 时出错：{str(e)}")  # 使用 WARNING 级别记录错误
        raise e

    # 返回采集数量和最新的消息 ID
    if collected_count > 0:
        logging.info(f"频道 {channel} 的最后一条消息 ID 更新为：{latest_message_id}")
    else:
        logging.info(f"频道 {channel} 未采集到新消息，最后一条消息 ID 保持不变：{latest_message_id}")

    return collected_count, latest_message_id

async def collect_data():
    """Telegram数据采集（包含图片下载）"""
    try:
        client = await get_client()
        logging.info("Telegram客户端已启动，开始采集...")

        os.makedirs(image_dir, exist_ok=True)

        try:
            with open(json_file_path, 'r', encoding='utf-8') as f:
                table_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            table_data = []

        # 加载持久化的唯一键集合
        unique_keys = load_unique_keys()

        # 加载每个频道的最后一条消息 ID
        last_message_ids = load_last_message_ids()

        # 创建锁对象
        lock = asyncio.Lock()

        # 并发处理多个频道的消息
        tasks = []
        for channel in channel_usernames:
            last_message_id = last_message_ids.get(channel, 0)
            logging.info(f"正在采集频道：{channel}，从消息 ID {last_message_id} 开始")
            tasks.append(process_channel(client, channel, table_data, unique_keys, lock, last_message_id))

        # 等待所有频道处理完成
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_collected = 0
        for i, result in enumerate(results):
            channel = channel_usernames[i]  # 获取当前频道
            if not isinstance(result, Exception):
                collected_count, latest_message_id = result
                total_collected += collected_count
                # 更新当前频道的最后一条消息 ID
                last_message_ids[channel] = latest_message_id
                logging.info(f"频道 {channel} 的最后一条消息 ID 更新为：{latest_message_id}")

        # 保存最后一条消息 ID
        save_last_message_ids(last_message_ids)

        # 保存唯一键集合
        save_unique_keys(unique_keys)

        # 最终保存数据
        save_data(table_data)
        logging.info(f"采集完成！共新增 {total_collected} 条记录")

    except Exception as e:
        logging.error(f"采集过程中出现严重错误：{str(e)}")  # 使用 ERROR 级别记录严重错误
    finally:
        await client.disconnect()

# ------------------------- 主程序 -------------------------
async def main():
    """主流程"""
    logging.info(f"\n{'='*40}")
    logging.info(f"Telegram资源采集器 v2.3 (Build {time.strftime('%Y%m%d')})")
    logging.info(f"{'='*40}")
    
    await collect_data()

if __name__ == "__main__":
    start_time = time.time()
    asyncio.run(main())
    logging.info(f"\n当前时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"总耗时：{time.time()-start_time:.2f}秒")
    logging.info("\n" + "="*40)
    logging.info("程序执行完毕，感谢使用！")
    logging.info("版权声明：本工具仅供学习交流，禁止用于任何商业用途")
    logging.info("="*40)

    # 刷新日志缓冲区
    logging.shutdown()
