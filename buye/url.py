#!/usr/bin/env python3
import json
import os
import requests
import warnings
import re
import logging
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning, MaxRetryError

# 忽略SSL警告
warnings.simplefilter('ignore', InsecureRequestWarning)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 获取脚本文件所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 站点映射关系
site_mappings = {
    '立播': 'libo',
    '闪电': 'shandian',
    '欧哥': 'ouge',
    '小米': 'xiaomi',
    '多多': 'duoduo',
    '蜡笔': 'labi',
    '至臻': 'zhizhen',
    '木偶': 'mogg',
    '六趣': 'liuqu',
    '虎斑': 'huban',
    '下饭': 'xiafan',
    '玩偶': 'wogg',
    '星剧社': 'star2'
}

buye_mappings = {
    '立播': 'libo',
    '闪电': 'sd',
    '欧哥': 'ouge',
    '小米': 'xmi',
    '多多': 'duo',
    '蜡笔': 'labi',
    '至臻': 'zhiz',
    '木偶': 'muo',
    '六趣': 'liuq',
    '虎斑': 'hub',
    '下饭': 'xiaf',
    '玩偶': 'wogg',
    '星剧社': 'star2'
}

# 配置代理（可选）
PROXIES = {
    'http': 'http://192.168.50.108:7890',  # 替换为实际代理地址
    'https': 'http://192.168.50.108:7890'
}

# 配置重试机制
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session = requests.Session()
session.mount("http://", adapter)
session.mount("https://", adapter)

def test_url(url, use_proxy=False):
    """测试URL是否可用"""
    try:
        proxies = PROXIES if use_proxy else None
        response = session.get(url.strip(), timeout=10, verify=False, proxies=proxies)  # 增加超时时间
        return response.status_code == 200
    except MaxRetryError as e:
        logging.warning(f"连接失败，已达到最大重试次数: {url} - {e}")
        return False
    except Exception as e:
        logging.error(f"测试URL失败: {url} - {e}")
        return False

def get_best_url(urls, use_proxy=False):
    """从多个URL中选择最佳的一个"""
    if not isinstance(urls, list):
        return urls.strip()
    
    for url in urls:
        if test_url(url, use_proxy):
            return url.strip()
    return None

def get_star2_real_url(source_url):
    """从源站获取星剧社真实链接"""
    try:
        response = session.get(source_url, timeout=10, verify=False)
        if response.status_code == 200:
            match = re.search(r'https?://[^"\'\s<>]+?star2\.cn[^"\'\s<>]*', response.text)
            if match:
                real_url = match.group(0).strip()
                logging.info(f"从源站获取到星剧社真实链接: {real_url}")
                return real_url
    except MaxRetryError as e:
        logging.warning(f"连接失败，已达到最大重试次数: {source_url} - {e}")
    except Exception as e:
        logging.error(f"获取星剧社真实链接失败: {e}")
    return None

def download_yuan_json():
    """下载线上 yuan.json 文件"""
    try:
        res = session.get('https://github.catvod.com/https://raw.githubusercontent.com/celin1286/xiaosa/refs/heads/main/yuan.json', verify=False)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logging.error(f"下载线上 yuan.json 文件失败: {e}")
        return None

def load_local_yuan_json():
    """加载本地 yuan.json 文件"""
    local_path = os.path.join(BASE_DIR, 'yuan.json')
    if os.path.exists(local_path):
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载本地 yuan.json 文件失败: {e}")
    return None

def save_local_yuan_json(data):
    """保存本地 yuan.json 文件"""
    local_path = os.path.join(BASE_DIR, 'yuan.json')
    try:
        with open(local_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"本地 yuan.json 文件已保存到 {local_path}")
    except Exception as e:
        logging.error(f"保存本地 yuan.json 文件失败: {e}")

def compare_and_update_yuan_json():
    """比较并更新本地 yuan.json 文件"""
    online_data = download_yuan_json()
    if not online_data:
        return False
    
    local_data = load_local_yuan_json()
    if local_data != online_data:
        save_local_yuan_json(online_data)
        logging.info("本地 yuan.json 文件已更新")
        return True  # 文件有变化
    else:
        logging.info("本地 yuan.json 文件无需更新")
        return False  # 文件无变化

def process_urls(existing_urls):
    """处理所有URL数据"""
    url_data = {}
    buye_data = {}
    try:
        yuan_data = load_local_yuan_json()
        if not yuan_data:
            logging.error("本地 yuan.json 文件无效或不存在")
            return False
        
        # 检查 yuan.json 的键是否在 site_mappings 或 buye_mappings 中
        for cn_name in yuan_data.keys():
            if cn_name not in site_mappings:
                logging.warning(f"警告: yuan.json 中的键 '{cn_name}' 未在 site_mappings 中找到映射")
            if cn_name not in buye_mappings:
                logging.warning(f"警告: yuan.json 中的键 '{cn_name}' 未在 buye_mappings 中找到映射")
        
        # 检查 site_mappings 和 buye_mappings 的键是否在 yuan.json 中
        for cn_name in site_mappings.keys():
            if cn_name not in yuan_data:
                logging.warning(f"警告: site_mappings 中的键 '{cn_name}' 未在 yuan.json 中找到")
        for cn_name in buye_mappings.keys():
            if cn_name not in yuan_data:
                logging.warning(f"警告: buye_mappings 中的键 '{cn_name}' 未在 yuan.json 中找到")
        
        base_data = {}
        for cn_name, urls in yuan_data.items():
            if cn_name not in site_mappings or cn_name not in buye_mappings:
                continue  # 跳过未映射的键
            
            if urls:
                if cn_name == '星剧社':
                    source_url = get_best_url(urls)
                    if source_url:
                        real_url = get_star2_real_url(source_url)
                        if real_url:
                            base_data[cn_name] = real_url
                            logging.info(f"添加 {cn_name} 链接: {real_url}")
                        else:
                            base_data[cn_name] = existing_urls.get(site_mappings[cn_name], "")
                            logging.info(f"保持 {cn_name} 原有链接")
                elif cn_name == '木偶':
                    # 特别处理木偶站点，尝试使用代理
                    best_url = get_best_url(urls, use_proxy=True)
                    if best_url:
                        base_data[cn_name] = best_url
                        logging.info(f"添加 {cn_name} 链接: {best_url}")
                    else:
                        base_data[cn_name] = existing_urls.get(site_mappings[cn_name], "")
                        logging.info(f"保持 {cn_name} 原有链接")
                else:
                    best_url = get_best_url(urls)
                    if best_url:
                        base_data[cn_name] = best_url
                        logging.info(f"添加 {cn_name} 链接: {best_url}")
                    else:
                        base_data[cn_name] = existing_urls.get(site_mappings[cn_name], "")
                        logging.info(f"保持 {cn_name} 原有链接")
        
        for cn_name, url in base_data.items():
            if cn_name in site_mappings:
                url_data[site_mappings[cn_name]] = url
            if cn_name in buye_mappings:
                buye_data[buye_mappings[cn_name]] = url
        
        if url_data:
            post_redirect_data(url_data)
            save_files(url_data, buye_data)
            logging.info("成功更新 url.json 和 buye.json")
            return True
        logging.info("没有新的有效数据")
        return False
    except Exception as e:
        logging.error(f"处理出错: {e}")
        return False

def save_files(url_data, buye_data):
    """保存数据到文件"""
    url_path = os.path.join(BASE_DIR, 'url.json')
    buye_path = os.path.join(BASE_DIR, 'buye.json')
    
    with open(url_path, 'w', encoding='utf-8') as f:
        json.dump(url_data, f, ensure_ascii=False, indent=2)
    
    with open(buye_path, 'w', encoding='utf-8') as f:
        json.dump(buye_data, f, ensure_ascii=False, indent=2)
    
    logging.info(f"文件已保存到 {BASE_DIR}")

def get_redirect_data():
    """获取现有重定向数据"""
    try:
        res = session.get('http://localhost:8080/redirect/', params={'token': 'abc123'}, verify=False)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logging.error(f"读取重定向数据失败: {e}")
        return {}

def post_redirect_data(data):
    """提交数据到接口"""
    try:
        res = session.post('http://localhost:8080/redirect/', params={'token': 'abc123'}, json=data, verify=False)
        res.raise_for_status()
        logging.info("更新重定向数据成功！")
    except Exception as e:
        logging.error(f"提交数据失败: {e}")

def main():
    """主函数"""
    logging.info("开始检查 yuan.json 文件...")
    if compare_and_update_yuan_json():  # 只有文件有变化时才运行后续逻辑
        logging.info("本地 yuan.json 文件有变化，开始更新 URL...")
        existing_urls = get_redirect_data()
        if process_urls(existing_urls):
            logging.info("更新完成")
        else:
            logging.info("更新失败，保持文件不变")
    else:
        logging.info("本地 yuan.json 文件无变化，无需更新")

if __name__ == "__main__":
    main()
