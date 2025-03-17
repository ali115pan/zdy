import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import time
import random
import re

# 文件路径
db_file = os.path.join(os.path.dirname(__file__), 'db.ini')

# 读取已有的影片名称
def read_existing_titles():
    if os.path.exists(db_file):
        with open(db_file, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

# 过滤影片名称
def filter_titles(titles):
    filtered_titles = set()
    for title in titles:
        # 删除纯数字（包括中文数字）
        if re.match(r'^[\d一二三四五六七八九十百千万亿]+$', title):
            continue
        # 删除纯英文
        if re.match(r'^[A-Za-z]+$', title):
            continue
        # 删除名称长度小于 3 个字符的
        if len(title) < 3:
            continue
        filtered_titles.add(title)
    return filtered_titles

# 写入影片名称到文件
def write_titles_to_file(titles):
    filtered_titles = filter_titles(titles)  # 过滤影片名称
    with open(db_file, 'w', encoding='utf-8') as f:
        for title in sorted(filtered_titles):  # 按字母顺序排序
            f.write(f"{title}\n")

# 提取影片名称和更新时间
def extract_titles_and_time(url, time_threshold=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    max_retries = 3
    retries = 0

    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # 检查请求是否成功
            break
        except requests.exceptions.RequestException as e:
            print(f"请求失败: {e}, 重试 {retries + 1}/{max_retries}")
            retries += 1
            time.sleep(5)  # 等待 5 秒后重试
    else:
        print(f"请求失败，已达到最大重试次数 {max_retries}")
        return set(), False, time_threshold

    soup = BeautifulSoup(response.content, 'html.parser')
    titles = set()
    stop_paging = False  # 是否停止翻页的标志
    new_threshold = time_threshold  # 新的时间阈值
    
    for item in soup.find_all('li', class_='clearfix'):
        try:
            # 提取影片名称
            title_tag = item.find('h3', class_='title')
            if title_tag is None:
                continue
            title = title_tag.find('a')['title']
            
            # 提取更新时间
            time_tag = item.find('span', class_='time')
            if time_tag is None:
                continue
            update_time = time_tag.text.strip()
            
            # 将更新时间转换为 datetime 对象
            update_datetime = datetime.strptime(update_time, '%Y-%m-%d %H:%M:%S')
            
            # 如果时间阈值存在，判断是否需要停止翻页
            if time_threshold is not None:
                if update_datetime < time_threshold:
                    stop_paging = True  # 设置停止翻页标志
                    new_threshold = update_datetime  # 更新阈值
                    break  # 停止处理当前页
            
            titles.add(title)  # 添加到集合中
        except Exception as e:
            print(f"解析失败: {e}")
            continue
    
    return titles, stop_paging, new_threshold

# 主函数
def main():
    base_url = "https://heimuer.tv/index.php/index/index/page/{}.html"
    page = 1
    all_titles = read_existing_titles()  # 读取已有的影片名称
    
    # 根据 db.ini 是否存在决定采集逻辑
    if os.path.exists(db_file):
        # 如果 db.ini 存在，采集最近 2 天的数据
        time_threshold = datetime.now() - timedelta(days=2)  # 设置时间阈值为当前时间减去 2 天
        print("db.ini 存在，采集最近 2 天的数据")
    else:
        # 如果 db.ini 不存在，采集所有数据
        time_threshold = None  # 不设置时间阈值
        print("db.ini 不存在，采集所有数据")
    
    while True:
        url = base_url.format(page)
        print(f"正在处理第 {page} 页: {url}")
        
        try:
            new_titles, stop_paging, new_threshold = extract_titles_and_time(url, time_threshold)
            if not new_titles:
                print(f"第 {page} 页没有数据，停止翻页")
                break
            
            # 合并新提取的影片名称
            all_titles.update(new_titles)
            
            # 更新时间阈值
            if time_threshold is not None:
                time_threshold = new_threshold
            
            # 检查是否需要停止翻页
            if stop_paging:
                print(f"发现更新时间小于 {time_threshold} 的影片，停止翻页")
                break
            
            page += 1
            time.sleep(random.uniform(1, 3))  # 随机延迟 1 到 3 秒
        except Exception as e:
            print(f"处理第 {page} 页时出错: {e}")
            continue
    
    # 写入去重后的影片名称到文件
    write_titles_to_file(all_titles)
    print(f"共提取 {len(all_titles)} 个影片名称，已保存到 {db_file}")

if __name__ == "__main__":
    main()