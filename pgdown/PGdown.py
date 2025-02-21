import os
import time
import shutil
import filecmp
import configparser
import zipfile
import re
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged
from telethon.sessions import StringSession
from socks import SOCKS5

# 获取当前脚本所在的绝对路径
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.ini')

# 读取配置文件
config = configparser.ConfigParser()
config.read(config_path)

# 设置工作目录为脚本所在目录
os.chdir(script_dir)

# 初始化Telegram客户端
api_id = config.getint('Telegram', 'api_id')
api_hash = config.get('Telegram', 'api_hash')
string_session = config.get('Telegram', 'string_session')
channel_username = config.get('Telegram', 'channel_username')
group_username = config.get('Telegram', 'group_username')  # 获取群组用户名
proxy_host = config.get('Proxy', 'host')
proxy_port = config.getint('Proxy', 'port')

# 设置 socks5 代理
proxy = (SOCKS5, proxy_host, proxy_port)

# 初始化客户端
client = TelegramClient(
    session=StringSession(string_session),
    api_id=api_id,
    api_hash=api_hash,
    connection=ConnectionTcpAbridged,
    proxy=proxy
)

# 获取当前目录下的文件
current_dir = os.getcwd()
zip_files = [f for f in os.listdir(current_dir) if f.endswith('.zip')]
exclude_files = config.get('Exclude', 'files').split(',')
exclude_extensions = config.get('Exclude', 'extensions').split(',')

# 记录已下载的附件名
downloaded_attachments_file = os.path.join(script_dir, 'downloaded_attachments.txt')

# 检查附件文件名是否符合格式
def is_valid_attachment_name(file_name):
    # 正则表达式匹配 pg.YYYYMMDD 格式
    pattern = r'^pg\.\d{8}'
    return re.match(pattern, file_name) is not None

# 获取频道的最新消息
async def get_latest_message():
    channel = await client.get_entity(channel_username)
    messages = await client(GetHistoryRequest(
        peer=channel,
        limit=1,
        offset_date=None,
        offset_id=0,
        max_id=0,
        min_id=0,
        add_offset=0,
        hash=0
    ))
    return messages.messages[0] if messages.messages else None

# 下载文件并显示进度
async def download_file(message, file_name):
    file_size = message.media.document.size  # 文件大小（字节）
    file_size_mb = file_size / (1024 * 1024)  # 转换为MB
    downloaded_size = 0
    start_time = time.time()

    # 使用更大的块大小以提高下载速度
    chunk_size = 1024 * 1024  # 1MB
    with open(file_name, 'wb') as f:
        async for chunk in client.iter_download(message.media.document, chunk_size=chunk_size):
            f.write(chunk)
            downloaded_size += len(chunk)
            elapsed_time = time.time() - start_time
            downloaded_size_mb = downloaded_size / (1024 * 1024)  # 转换为MB
            download_speed_mb = downloaded_size_mb / elapsed_time  # MB/s
            progress = (downloaded_size / file_size) * 100
            # 在同一行动态更新进度
            print(
                f"已下载: {downloaded_size_mb:.2f} MB, 总大小: {file_size_mb:.2f} MB, 进度: {progress:.2f}%, 速度: {download_speed_mb:.2f} MB/s",
                end="\r"
            )
    # 下载完成后换行
    print()
    return downloaded_size == file_size

# 解压 ZIP 文件并保持时间戳
def extract_zip_with_timestamps(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for zip_info in zip_ref.infolist():
            zip_ref.extract(zip_info, extract_to)
            file_path = os.path.join(extract_to, zip_info.filename)
            mod_time = time.mktime(zip_info.date_time + (0, 0, -1))
            os.utime(file_path, (mod_time, mod_time))

# 拷贝文件并保持时间戳
def copy_with_timestamps(src, dst):
    shutil.copy2(src, dst)
    src_stat = os.stat(src)
    os.utime(dst, (src_stat.st_atime, src_stat.st_mtime))

# 对比文件内容
def compare_files(file1, file2):
    if not os.path.exists(file1) or not os.path.exists(file2):
        return False
    with open(file1, 'r') as f1, open(file2, 'r') as f2:
        return f1.read() == f2.read()

# 对比文件内容并记录变化
def compare_files_and_log_changes(file1, file2):
    added_lines = []
    deleted_lines = []
    if os.path.exists(file1) and os.path.exists(file2):
        with open(file1, 'r') as f1, open(file2, 'r') as f2:
            old_lines = f1.readlines()
            new_lines = f2.readlines()
            added_lines = [line for line in new_lines if line not in old_lines]
            deleted_lines = [line for line in old_lines if line not in new_lines]
    return added_lines, deleted_lines

# 发送消息到群组
async def send_message_in_parts(client, group_username, message):
    # 将消息拆分为多个部分（Telegram 消息长度限制为 4096 字符）
    max_length = 4096
    parts = [message[i:i + max_length] for i in range(0, len(message), max_length)]
    for part in parts:
        await client.send_message(group_username, part)

# 同步文件并记录变化
def sync_files(src_dir, dst_dir):
    update_files = []  # 记录更新的文件
    deleted_files = []  # 记录删除的文件
    change_log_jsm = ""  # 记录 jsm.json 的变化内容
    change_log_tokentemplate = ""  # 记录 tokentemplate.json 的变化内容

    # 对比 jsm.json
    jsm_src = os.path.join(src_dir, 'jsm.json')
    jsm_dst = os.path.join(dst_dir, 'jsm.json')
    if os.path.exists(jsm_src) and os.path.exists(jsm_dst):
        added_lines, deleted_lines = compare_files_and_log_changes(jsm_dst, jsm_src)
        if added_lines or deleted_lines:
            print("jsm.json 文件内容有变化，更新中...")
            change_log_jsm = ""
            if added_lines:
                change_log_jsm += "新增内容如下：\n" + "\n".join([line.rstrip() for line in added_lines]) + "\n"
            if deleted_lines:
                change_log_jsm += "删除内容如下：\n" + "\n".join([line.rstrip() for line in deleted_lines]) + "\n"
            copy_with_timestamps(jsm_src, jsm_dst)
            update_files.append('jsm.json')

    # 对比 tokentemplate.json
    token_src = os.path.join(src_dir, 'lib', 'tokentemplate.json')
    token_dst = os.path.join(dst_dir, 'tokentemplate.json')
    if os.path.exists(token_src) and os.path.exists(token_dst):
        added_lines, deleted_lines = compare_files_and_log_changes(token_dst, token_src)
        if added_lines or deleted_lines:
            print("tokentemplate.json 文件内容有变化，更新中...")
            change_log_tokentemplate = ""
            if added_lines:
                change_log_tokentemplate += "新增内容如下：\n" + "\n".join([line.rstrip() for line in added_lines]) + "\n"
            if deleted_lines:
                change_log_tokentemplate += "删除内容如下：\n" + "\n".join([line.rstrip() for line in deleted_lines]) + "\n"
            copy_with_timestamps(token_src, token_dst)
            update_files.append('tokentemplate.json')

    # 同步 pg.jar、jsm.json、pg.jar.md5
    for file in ['pg.jar', 'jsm.json', 'pg.jar.md5']:
        src = os.path.join(src_dir, file)
        dst = os.path.join(dst_dir, file)
        if os.path.exists(src):
            if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
                print(f"更新文件: {dst}")
                copy_with_timestamps(src, dst)
                update_files.append(file)

    # 同步 ./pgdown/lib 目录下的所有文件
    lib_src = os.path.join(src_dir, 'lib')
    if os.path.exists(lib_src):
        for file in os.listdir(lib_src):
            src = os.path.join(lib_src, file)
            dst = os.path.join(dst_dir, file)
            if os.path.isfile(src):
                # 排除特定文件
                if file in exclude_files or any(file.endswith(ext) for ext in exclude_extensions):
                    continue
                if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
                    print(f"更新文件: {dst}")
                    copy_with_timestamps(src, dst)
                    update_files.append(file)

        # 删除目标目录中多余的文件（排除特定文件）
        for dst_file in os.listdir(dst_dir):
            dst_file_path = os.path.join(dst_dir, dst_file)
            if os.path.isfile(dst_file_path):
                # 排除特定文件
                if dst_file in exclude_files or any(dst_file.endswith(ext) for ext in exclude_extensions):
                    continue
                src_file = os.path.join(lib_src, dst_file)
                if not os.path.exists(src_file):
                    print(f"删除文件: {dst_file_path}")
                    safe_remove(dst_file_path)
                    deleted_files.append(dst_file)

    return update_files, deleted_files, change_log_jsm, change_log_tokentemplate

# 安全删除文件
def safe_remove(file_path):
    if os.path.exists(file_path) and os.path.abspath(file_path).startswith(script_dir):
        os.remove(file_path)
    else:
        print(f"安全警告：尝试删除的文件路径不在脚本目录内，操作已取消: {file_path}")

# 安全删除目录
def safe_rmtree(dir_path):
    if os.path.exists(dir_path) and os.path.abspath(dir_path).startswith(script_dir):
        shutil.rmtree(dir_path)
    else:
        print(f"安全警告：尝试删除的目录路径不在脚本目录内，操作已取消: {dir_path}")

# 记录已下载的附件名
def record_downloaded_attachment(attachment_name):
    with open(downloaded_attachments_file, 'a') as f:
        f.write(attachment_name + '\n')

# 检查附件是否已下载
def is_attachment_downloaded(attachment_name):
    if not os.path.exists(downloaded_attachments_file):
        return False
    with open(downloaded_attachments_file, 'r') as f:
        downloaded_attachments = f.read().splitlines()
        return attachment_name in downloaded_attachments

# 主逻辑
async def main():
    # 记录脚本开始运行时间
    start_time = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"脚本开始运行时间: {start_time}")

    latest_message = await get_latest_message()
    if latest_message and latest_message.media:
        attachment_name = latest_message.media.document.attributes[0].file_name
        if attachment_name.endswith('.zip'):
            # 检查附件文件名是否符合格式
            if not is_valid_attachment_name(attachment_name):
                print(f"附件文件名不符合格式要求: {attachment_name}")
                return  # 直接退出脚本

            # 检查附件是否已下载
            if is_attachment_downloaded(attachment_name):
                print(f"附件 {attachment_name} 已下载，跳过下载。")
                return

            if attachment_name not in zip_files:
                print(f"开始下载 {attachment_name}...")
                for attempt in range(5):
                    if await download_file(latest_message, attachment_name):
                        print("下载成功！")
                        # 记录已下载的附件名
                        record_downloaded_attachment(attachment_name)
                        # 删除旧的 .zip 文件
                        for old_zip in zip_files:
                            print(f"删除旧的 .zip 文件: {old_zip}")
                            safe_remove(old_zip)
                        # 解压文件（静默模式）
                        if os.path.exists('./pgdown'):
                            safe_rmtree('./pgdown')
                        os.makedirs('./pgdown', exist_ok=True)
                        extract_zip_with_timestamps(attachment_name, './pgdown')  # 使用自定义解压函数

                        # 如果当前目录只有 PGdown.py、config.ini 和刚下载的 .zip 文件
                        current_files = [
                            f for f in os.listdir(current_dir)
                            if os.path.isfile(os.path.join(current_dir, f))  # 只检查文件，排除目录
                            and f not in ['PGdown.py', 'config.ini', attachment_name]
                        ]
                        if len(current_files) == 0:
                            print("当前目录无其他文件，直接拷贝...")
                            # 拷贝 pg.jar、jsm.json、pg.jar.md5
                            for file in ['pg.jar', 'jsm.json', 'pg.jar.md5']:
                                src = os.path.join('./pgdown', file)
                                if os.path.exists(src):
                                    copy_with_timestamps(src, current_dir)
                            # 拷贝 ./pgdown/lib 目录下的所有文件
                            lib_dir = os.path.join('./pgdown', 'lib')
                            if os.path.exists(lib_dir):
                                for file in os.listdir(lib_dir):
                                    src = os.path.join(lib_dir, file)
                                    if os.path.isfile(src):
                                        copy_with_timestamps(src, current_dir)

                            # 发送更新信息到群组
                            attachment_info = f"PG最新版本：{attachment_name}\n"
                            update_info = "无文件更新\n"
                            content_info = latest_message.message.split('今日更新内容', 1)[1] if latest_message.message and '今日更新内容' in latest_message.message else "无更新内容"
                            full_message = attachment_info + update_info + "今日更新内容：\n" + content_info
                            await send_message_in_parts(client, group_username, full_message)
                            print(f"更新信息已转发到群组：{group_username}")
                        else:
                            print("当前目录有其他文件，进行对比更新...")
                            update_files, deleted_files, change_log_jsm, change_log_tokentemplate = sync_files('./pgdown', current_dir)

                            # 发送更新信息到群组
                            attachment_info = f"PG最新版本：{attachment_name}\n"
                            update_info = f"更新的文件有：{', '.join(update_files)}\n" if update_files else "无文件更新\n"
                            if deleted_files:
                                update_info += f"已删除的文件有：{', '.join(deleted_files)}\n"
                            content_info = latest_message.message.split('今日更新内容', 1)[1] if latest_message.message and '今日更新内容' in latest_message.message else "无更新内容"
                            if 'jsm.json' in update_files and change_log_jsm:
                                update_info += f"jsm.json文件变化内容：\n{change_log_jsm}\n"
                            if 'tokentemplate.json' in update_files and change_log_tokentemplate:
                                update_info += f"tokentemplate.json文件变化内容：\n{change_log_tokentemplate}\n"
                            full_message = attachment_info + update_info + "今日更新内容：\n" + content_info
                            await send_message_in_parts(client, group_username, full_message)
                            print(f"更新信息已转发到群组：{group_username}")
                        break
                    else:
                        print(f"第 {attempt + 1} 次下载失败，正在重试...")
                else:
                    print("下载失败超过 5 次，删除文件并退出脚本...")
                    safe_remove(attachment_name)
            else:
                print("最新附件已下载，无需重复下载。")
        else:
            print("最新消息中未找到 .zip 附件。")
    else:
        print("最新消息中未找到媒体文件。")

    # 记录脚本结束运行时间
    end_time = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"脚本结束运行时间: {end_time}")

# 运行客户端
with client:
    client.loop.run_until_complete(main())