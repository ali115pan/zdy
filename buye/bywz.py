#!/usr/bin/env python3
import json
import re
import requests
import logging
import os
import time
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 禁用 SSL 警告
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# 加载配置文件
def load_config() -> Dict:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'bywz.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载配置文件失败: {str(e)}")
        return {}

config = load_config()

# URL配置
WOGG_SOURCE_URL = config.get("wogg_source_url", "")
XJS_SOURCE_URL = config.get("xjs_source_url", "")
GITHUB_YUAN_JSON_URL = config.get("github_yuan_json_url", "")
REDIRECT_URL = config.get("redirect_url", "")
REDIRECT_TOKEN = config.get("redirect_token", "")

# 站点映射关系
site_mappings = config.get("site_mappings", {})

def get_script_directory() -> str:
    """获取脚本所在目录"""
    return os.path.dirname(os.path.abspath(__file__))

def save_to_json(data: dict, filename: str):
    """将数据保存为JSON文件"""
    script_dir = get_script_directory()
    file_path = os.path.join(script_dir, filename)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"数据已成功保存到文件: {file_path}")
    except Exception as e:
        logger.error(f"保存JSON文件失败: {str(e)}")

def fetch_url(url: str, verify: bool = False) -> Optional[str]:
    """通用函数：获取URL内容"""
    try:
        response = requests.get(url, verify=verify)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logger.error(f"获取URL内容失败: {url}, 错误: {str(e)}")
    return None

def fetch_github_yuan_json() -> Optional[Dict]:
    """从GitHub获取yuan.json文件"""
    content = fetch_url(GITHUB_YUAN_JSON_URL)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"解析yuan.json失败: {str(e)}")
    return None

def read_yuan_json() -> Dict[str, List[str]]:
    """读取yuan.json文件，如果不存在或与远程文件不一致，则从GitHub下载"""
    script_dir = get_script_directory()
    yuan_file_path = os.path.join(script_dir, 'yuan.json')
    
    # 尝试从GitHub获取最新文件
    github_yuan_data = fetch_github_yuan_json()
    if not github_yuan_data:
        logger.error("无法从GitHub获取yuan.json文件，将使用本地文件（如果存在）")
        if os.path.exists(yuan_file_path):
            try:
                with open(yuan_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"读取本地yuan.json文件失败: {str(e)}")
                return {}
        else:
            logger.error("本地也没有找到yuan.json文件，程序无法继续")
            return {}
    
    # 如果GitHub文件获取成功，检查本地文件是否存在
    if os.path.exists(yuan_file_path):
        try:
            with open(yuan_file_path, 'r', encoding='utf-8') as f:
                local_yuan_data = json.load(f)
        except Exception as e:
            logger.error(f"读取本地yuan.json文件失败: {str(e)}")
            local_yuan_data = {}
        
        # 对比本地文件与远程文件
        if local_yuan_data != github_yuan_data:
            logger.info("检测到GitHub上的yuan.json文件与本地文件不一致，将更新本地文件")
            save_to_json(github_yuan_data, 'yuan.json')
        else:
            logger.info("本地yuan.json文件与GitHub上的文件一致，无需更新")
    else:
        # 如果本地没有文件，直接保存远程文件
        logger.info("本地没有找到yuan.json文件，已从GitHub下载并保存")
        save_to_json(github_yuan_data, 'yuan.json')
    
    return github_yuan_data

def get_initial_wogg_url() -> str:
    """从源站获取玩偶初始链接"""
    content = fetch_url(WOGG_SOURCE_URL)
    if content:
        match = re.search(r'href="(https://[^"]*?wogg[^"]*?)"', content)
        if match:
            initial_url = match.group(1).rstrip('/')
            logger.info(f"从源站获取到玩偶初始链接: {initial_url}")
            return initial_url
    return "https://wogg.xxooo.cf"

def get_wogg_urls() -> List[str]:
    """获取玩偶的所有有效链接"""
    initial_url = get_initial_wogg_url()
    content = fetch_url(initial_url)
    if content:
        domains = []
        notice_match = re.search(r'<div class="popup-main">(.*?)</div>', content, re.DOTALL)
        if notice_match:
            for pattern in [r'域名\s+((?:www\.)?wogg\.[a-z.]+)', r'备用\s+((?:www\.)?wogg\.[a-z.]+)']:
                domains.extend(re.findall(pattern, notice_match.group(1)))
        domains = list(dict.fromkeys(domains))  # 去重
        if domains:
            return [f"https://{domain.strip('/')}" for domain in domains]
    return [initial_url]

def get_xjs_url() -> Optional[str]:
    """从源站获取星剧社链接"""
    content = fetch_url(XJS_SOURCE_URL)
    if content:
        match = re.search(r'https?://[^"\'\s<>]+?star2\.cn[^"\'\s<>]*', content)
        if match:
            url = match.group(0)
            logger.info(f"找到星剧社域名: {url}")
            return url.rstrip('/')
    return None

def measure_speed(url: str, max_retries: int = 3, timeout: tuple = (5, 15)) -> float:
    """测量单个链接的响应时间，增加重试机制和超时调整"""
    session = requests.Session()
    retries = Retry(total=max_retries, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))

    try:
        start_time = time.time()
        response = session.get(url, verify=False, timeout=timeout)
        if response.status_code == 200:
            elapsed_time = time.time() - start_time
            logger.info(f"链接: {url}, 响应时间: {elapsed_time:.2f}秒")
            return elapsed_time
        else:
            logger.warning(f"链接 {url} 响应状态码: {response.status_code}")
            return float('inf')  # 如果状态码不是200，返回无穷大
    except requests.exceptions.Timeout as e:
        logger.error(f"测速超时，链接: {url}, 错误: {str(e)}")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"连接失败，链接: {url}, 错误: {str(e)}")
    except Exception as e:
        logger.error(f"测速失败，链接: {url}, 错误: {str(e)}")
    return float('inf')  # 出错时返回无穷大

def select_fastest_link(urls: List[str]) -> Optional[str]:
    """从多个链接中选择速度最快的链接"""
    if not urls:
        return None
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {executor.submit(measure_speed, url): url for url in urls}
        fastest_url = None
        fastest_time = float('inf')
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                speed = future.result()
                if speed < fastest_time:
                    fastest_time = speed
                    fastest_url = url
            except Exception as e:
                                logger.error(f"测速失败，链接: {url}, 错误: {str(e)}")
        return fastest_url

def main() -> bool:
    """主函数"""
    logger.info("开始获取链接...")
    
    # 读取yuan.json文件，如果不存在或与远程文件不一致，则从GitHub下载
    yuan_data = read_yuan_json()
    if not yuan_data:
        logger.error("yuan.json文件为空或读取失败")
        return False
    
    # 获取玩偶链接
    wogg_urls = get_wogg_urls()
    logger.info(f"获取到玩偶链接: {wogg_urls}")
    
    # 获取星剧社链接
    xjs_url = get_xjs_url()
    logger.info(f"获取到星剧社链接: {xjs_url}")
    
    # 替换yuan.json中的内容
    if '玩偶' in yuan_data:
        yuan_data['玩偶'] = wogg_urls
    if '星剧社' in yuan_data:
        yuan_data['星剧社'] = [xjs_url] if xjs_url else []
    
    # 读取现有的url.json文件（如果存在）
    script_dir = get_script_directory()
    url_json_path = os.path.join(script_dir, 'url.json')
    existing_url_data = {}
    if os.path.exists(url_json_path):
        try:
            with open(url_json_path, 'r', encoding='utf-8') as f:
                existing_url_data = json.load(f)
        except Exception as e:
            logger.error(f"读取现有url.json文件失败: {str(e)}")
    
    # 检查yuan.json和site_mappings的对应关系
    url_json_data = {}
    for category, urls in yuan_data.items():
        mapped_category = site_mappings.get(category, category)  # 获取映射后的分类名称
        if not urls:
            logger.warning(f"分类 {category} 没有获取到链接，跳过测速")
            # 如果分类没有链接，保留现有值（如果存在）
            existing_value = existing_url_data.get(mapped_category, "")
            url_json_data[mapped_category] = existing_value
            continue
        
        # 测速并选择最快的链接
        fastest_url = select_fastest_link(urls)
        if fastest_url:
            url_json_data[mapped_category] = fastest_url
        else:
            # 如果测速失败，保留现有的值（如果存在）
            existing_value = existing_url_data.get(mapped_category, "")
            url_json_data[mapped_category] = existing_value
            logger.warning(f"分类 {category} 测速失败，保留现有值: {existing_value}")
    
    # 处理site_mappings中多出的分类
    for mapped_category in site_mappings.values():
        if mapped_category not in url_json_data:
            # 如果site_mappings中多出的分类在url.json中存在，则保留现有值
            existing_value = existing_url_data.get(mapped_category, "")
            url_json_data[mapped_category] = existing_value
            logger.warning(f"分类 {mapped_category} 在yuan.json中不存在，保留现有值: {existing_value}")
    
    # 保存为url.json
    save_to_json(url_json_data, 'url.json')
    return True

if __name__ == "__main__":
    ret = main()
    if ret:
        try:
            # 发送请求到指定的 URL
            response = requests.get(
                REDIRECT_URL, 
                params={'token': REDIRECT_TOKEN}, 
                verify=False
            )
            if response.status_code == 200:
                logger.info("成功触发删除重定向操作")
            else:
                logger.warning(f"触发删除重定向操作失败，状态码: {response.status_code}")
        except Exception as e:
            logger.error(f"发送请求失败: {str(e)}")
