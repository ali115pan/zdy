import os
import requests
import shutil
import subprocess
from bs4 import BeautifulSoup
from telethon import TelegramClient, sync
from telethon.sessions import StringSession
import json
import configparser
import asyncio
import datetime

# 读取配置文件
config = configparser.ConfigParser()
config.read('config.ini')

# 从配置文件中获取 Telegram 配置
api_id = config['telegram']['api_id']
api_hash = config['telegram']['api_hash']
string_session = config['telegram']['string_session']
group_name = config['telegram']['group_name']

# 从配置文件中获取代理配置
PROXY_HOST = config['proxy']['host']
PROXY_PORT = int(config['proxy']['port'])  # 将端口号转换为整数

# requests 库的代理配置
proxies = {
    'http': f'socks5://{PROXY_HOST}:{PROXY_PORT}',
    'https': f'socks5://{PROXY_HOST}:{PROXY_PORT}'
}

# 从配置文件中获取 GitHub 配置
repo_owner = config['github']['repo_owner']
repo_name = config['github']['repo_name']
personal_access_token = config['github']['personal_access_token']

# 从配置文件中获取镜像站点配置
mirror_sites = [config['mirror_sites'][key] for key in config['mirror_sites']]

# 创建 Telegram 客户端
client = TelegramClient(StringSession(string_session), api_id, api_hash, proxy=('socks5', PROXY_HOST, PROXY_PORT))

async def send_message(message):
    """
    发送消息到 Telegram 群组。
    """
    # 获取群组的 chat_id
    group_entity = await client.get_entity(group_name)
    chat_id = group_entity.id
    
    # 发送消息到指定的群组
    await client.send_message(chat_id, message, parse_mode='md')

def get_latest_release_info():
    """
    获取 GitHub 仓库的最新版本信息。
    """
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/releases/latest'
    headers = {'Authorization': f'token {personal_access_token}'} if personal_access_token else {}
    try:
        response = requests.get(url, headers=headers, proxies=proxies)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"直接获取失败，尝试通过代理获取：{e}")
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"请求失败：{e}")
            return None

    return response.json()

def download_file(url, filename, use_proxy=True):
    """
    下载文件。
    """
    try:
        # 如果 use_proxy 为 False，则不使用代理
        proxies_to_use = proxies if use_proxy else None
        response = requests.get(url, proxies=proxies_to_use, stream=True)
        response.raise_for_status()
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.RequestException as e:
        print(f"下载失败，尝试通过代理下载：{e}")
        try:
            # 如果镜像站点下载失败，尝试使用代理直接下载
            response = requests.get(url, proxies=proxies, stream=True)
            response.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except requests.RequestException as e:
            print(f"下载失败：{e}")
            return False

def backup_file(src, dst):
    """
    备份文件。
    """
    if os.path.exists(src):
        shutil.copy(src, dst)
        print(f"备份 {src} 到 {dst}")

def restore_file(src, dst):
    """
    恢复备份的文件。
    """
    if os.path.exists(src):
        shutil.copy(src, dst)
        print(f"已恢复 {src} 到 {dst}")

def copy_files_recursively(src, dst):
    """
    递归拷贝文件夹。
    """
    # 遍历源文件夹中的所有文件和文件夹
    for item in os.listdir(src):
        src_item = os.path.join(src, item)
        dst_item = os.path.join(dst, item)

        # 如果是文件夹，递归调用
        if os.path.isdir(src_item):
            if not os.path.exists(dst_item):
                os.makedirs(dst_item)
            copy_files_recursively(src_item, dst_item)
        # 如果是文件，直接拷贝
        elif os.path.isfile(src_item):
            shutil.copy2(src_item, dst_item)
            print(f"文件 {src_item} 已拷贝到 {dst_item}")

def get_file_commit_time(repo_owner, repo_name, path):
    """
    获取文件的最后提交时间。
    """
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits?path={path}"
    headers = {'Authorization': f'token {personal_access_token}'} if personal_access_token else {}
    try:
        response = requests.get(url, headers=headers, proxies=proxies)
        response.raise_for_status()
        commits = response.json()
        if commits:
            last_commit = commits[0]
            commit_time = last_commit['commit']['committer']['date']
            return datetime.datetime.strptime(commit_time, '%Y-%m-%dT%H:%M:%SZ').timestamp()
    except requests.RequestException as e:
        print(f"获取文件提交时间失败：{e}")
    return None

def update_local_files(repo_owner, repo_name, remote_path, local_path):
    """
    对比并更新本地文件。
    """
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{remote_path}"
    headers = {'Authorization': f'token {personal_access_token}'} if personal_access_token else {}
    try:
        response = requests.get(url, headers=headers, proxies=proxies)
        response.raise_for_status()
        remote_files = response.json()
    except requests.RequestException as e:
        print(f"获取 GitHub 文件列表失败：{e}")
        return None

    updated_files = []
    total_files = len(remote_files)
    for index, file in enumerate(remote_files):
        if file['type'] == 'file':
            file_name = file['name']
            download_url = file['download_url']
            file_path = file['path']

            # 获取文件的最后提交时间
            commit_time = get_file_commit_time(repo_owner, repo_name, file_path)
            if not commit_time:
                continue

            local_file_path = os.path.join(local_path, file_name)
            if os.path.exists(local_file_path):
                local_last_modified = os.path.getmtime(local_file_path)
                if local_last_modified >= commit_time:
                    print(f"文件 {file_name} 已是最新，跳过。")
                    continue  # 本地文件已是最新，跳过

            # 下载文件
            print(f"正在下载文件 {index + 1}/{total_files}: {file_name}")
            if download_file(download_url, local_file_path, use_proxy=False):
                # 设置文件的最后修改时间为 GitHub 上的提交时间
                os.utime(local_file_path, (commit_time, commit_time))
                updated_files.append(file_name)
                print(f"文件 {file_name} 更新成功。")
            else:
                print(f"文件 {file_name} 下载失败")

    return updated_files

def send_update_message(updated_files, path):
    """
    发送文件更新信息到 Telegram 群组。
    """
    if updated_files:
        message = f"以下文件已更新（{path}）：\n"
        message += "\n".join(updated_files)
        with client:
            client.loop.run_until_complete(send_message(message))
    else:
        print(f"没有文件需要更新（{path}），不发送消息。")

def update_js_and_libs_files(script_dir):
    """
    更新 js 和 libs 目录下的文件。
    """
    print("开始更新 js 和 libs 目录...")
    # 更新 js 目录
    updated_js_files = update_local_files(repo_owner, repo_name, 'js', os.path.join(script_dir, 'drpy-node/js'))
    send_update_message(updated_js_files, 'js')

    # 更新 libs 目录
    updated_libs_files = update_local_files(repo_owner, repo_name, 'libs', os.path.join(script_dir, 'drpy-node/libs'))
    send_update_message(updated_libs_files, 'libs')
    print("js 和 libs 目录更新完成。")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    drpy_node_dir = os.path.join(script_dir, "drpy-node")
    env_file = os.path.join(drpy_node_dir, "config", "env.json")
    backup_env_file = os.path.join(script_dir, "env_backup.json")
    dotenv_development_file = os.path.join(drpy_node_dir, ".env.development")
    backup_dotenv_development_file = os.path.join(script_dir, "env.development_backup")
    sub_file = os.path.join(drpy_node_dir, "public", "sub", "sub.json")
    backup_sub_file = os.path.join(script_dir, "sub_backup.json")
    zdy_file = os.path.join(drpy_node_dir, "public", "sub", "order_zdy.html")
    backup_zdy_file = os.path.join(script_dir, "order_zdy_backup.html")
    local_version_file = os.path.join(script_dir, "local_version.txt")
    docker_compose_file = os.path.join(script_dir, "docker-compose.yml")
    src_folder = os.path.join(script_dir, 'zdyjs')
    dst_folder = os.path.join(script_dir, 'drpy-node/js')
    
    # 获取最新版本信息
    release_info = get_latest_release_info()
    if not release_info:
        print("无法获取最新版本信息")
        return

    remote_version = release_info['tag_name']
    browser_download_url = next((asset['browser_download_url'] for asset in release_info['assets'] if not asset['name'].endswith('green')), None)
    if not browser_download_url:
        print("无法找到有效的下载链接")
        return

    print(f"解析得到的远程版本号: {remote_version}")

    # 读取本地版本号
    if not os.path.exists(local_version_file):
        print("本地版本号文件不存在，创建新文件并下载最新版本。")
        local_version = ""
    else:
        with open(local_version_file, 'r') as f:
            local_version = f.read().strip()
        print(f"本地版本号: {local_version}")

    # 比较版本号
    if local_version != remote_version:
        print(f"发现新版本: {remote_version}，正在下载...")

        # 使用镜像站点下载
        for site in mirror_sites:
            download_url = f"{site}/{browser_download_url}"
            download_path = os.path.join(script_dir, f"drpy-node-{remote_version}.7z")

            if download_file(download_url, download_path, use_proxy=False):
                print("下载完成。")
                break
        else:
            print("所有镜像站点下载失败，尝试通过代理直接下载...")
            if download_file(browser_download_url, download_path, use_proxy=True):
                print("通过代理下载完成。")
            else:
                print("所有下载尝试均失败，请检查网络连接和代理设置。")
                return

        # 更新本地版本号文件
        with open(local_version_file, 'w') as f:
            f.write(remote_version)

        # 备份配置文件
        backup_file(env_file, backup_env_file)
        backup_file(dotenv_development_file, backup_dotenv_development_file)
        backup_file(sub_file, backup_sub_file)
        backup_file(zdy_file, backup_zdy_file)
        
        # 删除旧的项目目录
        if os.path.exists(drpy_node_dir):
            shutil.rmtree(drpy_node_dir)
            print(f"已删除旧的 {drpy_node_dir} 文件夹")

        # 解压文件
        print("正在解压文件...")
        if subprocess.run(['7z', 'x', '-aoa', '-y', '-bd', '-bso0', '-bsp0', download_path, f"-o{drpy_node_dir}"], cwd=script_dir).returncode == 0:
            print("解压成功。")

            # 恢复备份的配置文件
            restore_file(backup_env_file, env_file)
            restore_file(backup_dotenv_development_file, dotenv_development_file)
            restore_file(backup_sub_file, sub_file)
            restore_file(backup_zdy_file, zdy_file)
            copy_files_recursively(src_folder, dst_folder)
            print("所有文件和文件夹已成功拷贝！")
            # 删除下载的压缩包
            os.remove(download_path)

            # 检查并启动 Docker 容器
            if os.path.exists(docker_compose_file):
                container_name = "drpyS"
                if subprocess.run(['docker', 'ps', '-a', '-f', f'name=^/{container_name}$'], stdout=subprocess.PIPE).stdout.decode().strip():
                    print("停止并移除现有容器...")
                    subprocess.run(['docker', 'compose', 'down'], cwd=script_dir)
                #修改支持drpy2
                file_path = os.path.join(script_dir, 'drpy-node/controllers/config.js')
                old_content = 'let api = `assets://js/lib/drpy2.js`'
                new_content = 'let api = `https://github.catvod.com/https://raw.githubusercontent.com/XXX/main/drpy2.min.js`'
                with open(file_path, 'r', encoding='utf-8') as file:
                     content = file.read()
                content = content.replace(old_content, new_content)
                with open(file_path, 'w', encoding='utf-8') as file:
                     file.write(content)
                print("启动 Docker 容器...")
                if subprocess.run(['docker', 'compose', 'up', '-d'], cwd=script_dir).returncode == 0:
                    print("Docker 容器启动成功。")
                    release_notes = release_info['body']
                    formatted_release_notes = f"# {remote_version}\n{release_notes}"
                    message = f"道长Drpys{formatted_release_notes}"
                    with client:
                        client.loop.run_until_complete(send_message(message))
                else:
                    print("Docker 容器启动失败，请检查。")
            else:
                print("docker-compose.yml 配置文件不存在，退出.")
        else:
            print("解压失败，请检查文件完整性。")
    else:
        print(f"当前已是最新版本: {remote_version}。")

    # 更新 js 和 libs 目录下的文件
    update_js_and_libs_files(script_dir)

if __name__ == '__main__':
    main()