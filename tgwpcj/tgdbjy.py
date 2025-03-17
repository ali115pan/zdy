import os
import re
import mysql.connector
import json
import logging
import asyncio
import httpx
from bs4 import BeautifulSoup
from logging.handlers import RotatingFileHandler
from asyncio import Semaphore

# 配置日志
log_dir = os.path.join(os.getcwd(), 'logs')  # 日志文件目录
os.makedirs(log_dir, exist_ok=True)  # 创建日志目录（如果不存在）
log_file = os.path.join(log_dir, 'dblink_validator.log')  # 日志文件路径

# 设置日志处理器
log_handler = RotatingFileHandler(
    log_file,
    maxBytes=10 * 1024 * 1024,  # 每个日志文件最大 10MB
    backupCount=5,  # 最多保留 5 个日志文件
    encoding='utf-8'
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[log_handler]
)
logger = logging.getLogger(__name__)

# 屏蔽 httpx 的 INFO 日志
logging.getLogger("httpx").setLevel(logging.WARNING)

# 文件路径
INVALID_JSON_PATH = os.path.join(os.getcwd(), "invalid_records.json")  # 存储无效记录的文件

# 图片存储路径
IMAGE_BASE_DIR = os.path.join(os.getcwd(), "data")  # 图片存储的根目录


def parse_database_php(file_path):
    """
    解析 database.php 文件，提取数据库连接信息
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # 使用正则表达式提取配置信息
    config = {
        'type': re.search(r"'type'\s*=>\s*'(\w+)'", content).group(1),
        'hostname': re.search(r"'hostname'\s*=>\s*'([^']+)'", content).group(1),
        'database': re.search(r"'database'\s*=>\s*'([^']+)'", content).group(1),
        'username': re.search(r"'username'\s*=>\s*'([^']+)'", content).group(1),
        'password': re.search(r"'password'\s*=>\s*'([^']+)'", content).group(1),
        'hostport': re.search(r"'hostport'\s*=>\s*'([^']+)'", content).group(1),
        'charset': re.search(r"'charset'\s*=>\s*'([^']+)'", content).group(1),
        'prefix': re.search(r"'prefix'\s*=>\s*'([^']+)'", content).group(1),
    }
    return config


def connect_to_database(config):
    """
    使用配置信息连接数据库
    """
    try:
        conn = mysql.connector.connect(
            host=config['hostname'],
            user=config['username'],
            password=config['password'],
            database=config['database'],
            port=config['hostport'],
            charset=config['charset']
        )
        return conn
    except mysql.connector.Error as err:
        logger.error(f"数据库连接失败: {err}")
        return None


def execute_query(conn, query):
    """
    执行 SQL 查询并返回结果
    """
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        results = cursor.fetchall()
        return results
    except mysql.connector.Error as err:
        logger.error(f"查询执行失败: {err}")
        return None
    finally:
        if cursor:
            cursor.close()


def delete_invalid_records(conn, vod_ids, vod_pics):
    """
    删除数据库中无效的记录，并删除对应的图片文件
    """
    cursor = None
    try:
        if not vod_ids:
            logger.info("没有无效记录需要删除")
            return

        cursor = conn.cursor()
        query = "DELETE FROM mac_vod WHERE vod_id IN (%s)" % ','.join(['%s'] * len(vod_ids))
        cursor.execute(query, vod_ids)
        conn.commit()
        logger.info(f"成功删除 {len(vod_ids)} 条无效记录")

        # 删除对应的图片文件
        for vod_pic in vod_pics:
            if vod_pic:
                # 修正图片路径拼接逻辑
                image_path = os.path.join(IMAGE_BASE_DIR, vod_pic.lstrip('/'))  # 去掉开头的斜杠
                if os.path.exists(image_path):
                    os.remove(image_path)
                    logger.info(f"成功删除图片文件: {image_path}")
                else:
                    logger.warning(f"图片文件不存在: {image_path}")
    except mysql.connector.Error as err:
        logger.error(f"删除记录失败: {err}")
    finally:
        if cursor:
            cursor.close()


def save_invalid_records(invalid_records):
    """
    将无效记录保存到 JSON 文件
    """
    try:
        with open(INVALID_JSON_PATH, 'w', encoding='utf-8') as file:
            json.dump(invalid_records, file, ensure_ascii=False, indent=4)
        logger.info(f"无效记录已保存到文件: {INVALID_JSON_PATH}")
    except Exception as e:
        logger.error(f"保存无效记录失败: {e}")


class LinkValidator:
    def __init__(self):
        pass

    def extract_share_id(self, url: str):
        """
        从链接中提取分享ID，支持多域名网盘
        :param url: 网盘链接
        :return: (share_id, service) 或 (None, None)
        """
        try:
            net_disk_patterns = {
                'uc': {
                    'domains': ['drive.uc.cn'],
                    'pattern': r"https?://drive\.uc\.cn/s/([a-zA-Z0-9]+)"
                },
                'aliyun': {
                    'domains': ['aliyundrive.com', 'alipan.com'],
                    'pattern': r"https?://(?:www\.)?(?:aliyundrive|alipan)\.com/s/([a-zA-Z0-9]+)"
                },
                'quark': {
                    'domains': ['pan.quark.cn'],
                    'pattern': r"https?://(?:www\.)?pan\.quark\.cn/s/([a-zA-Z0-9]+)"
                },
                '115': {
                    'domains': ['115.com', '115cdn.com', 'anxia.com'],
                    'pattern': r"https?://(?:www\.)?(?:115|115cdn|anxia)\.com/s/([a-zA-Z0-9]+)"
                },
                'baidu': {
                    'domains': ['pan.baidu.com', 'yun.baidu.com'],
                    'pattern': r"https?://(?:[a-z]+\.)?(?:pan|yun)\.baidu\.com/(?:s/|share/init\?surl=)([a-zA-Z0-9-]+)"
                },
                'pikpak': {
                    'domains': ['mypikpak.com'],
                    'pattern': r"https?://(?:www\.)?mypikpak\.com/s/([a-zA-Z0-9]+)"
                },
                '123': {
                    'domains': ['123684.com', '123685.com', '123865.com', '123912.com', '123pan.com', '123pan.cn', '123592.com'],
                    'pattern': r"https?://(?:www\.)?(?:123684|123685|123865|123912|123pan|123pan\.cn|123592)\.com/s/([a-zA-Z0-9-]+)"
                },
                'tianyi': {
                    'domains': ['cloud.189.cn'],
                    'pattern': r"https?://cloud\.189\.cn/(?:t/|web/share\?code=)([a-zA-Z0-9]+)"
                }
            }
            for net_disk, config in net_disk_patterns.items():
                if any(domain in url for domain in config['domains']):
                    match = re.search(config['pattern'], url)
                    if match:
                        share_id = match.group(1)
                        return share_id, net_disk
            return None, None
        except Exception as e:
            logger.error(f"Error extracting share ID from URL {url}: {e}")
            return None, None

    async def check_uc(self, share_id: str):
        """检测UC网盘链接有效性"""
        url = f"https://drive.uc.cn/s/{share_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.101 Mobile Safari/537.36",
            "Host": "drive.uc.cn",
            "Referer": url,
            "Origin": "https://drive.uc.cn",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(url, headers=headers)
                logger.info(f"请求UC链接: {url}, 状态码: {response.status_code}")
                if response.status_code != 200:
                    return False

                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text(strip=True)

                # 检查错误提示
                error_keywords = ["失效", "不存在", "违规", "删除", "已过期", "被取消"]
                if any(keyword in page_text for keyword in error_keywords):
                    return False

                # 检查是否需要访问码（有效但需密码）
                if soup.select_one(".main-body .input-wrap input"):
                    logger.info(f"UC链接 {url} 需要密码")
                    return True

                # 检查是否包含文件列表或分享内容（有效）
                if "文件" in page_text or "分享" in page_text or soup.select_one(".file-list"):
                    return True

                return False
        except httpx.RequestError as e:
            logger.error(f"UC检查错误 for {share_id}: {str(e)}")
            return False

    async def check_aliyun(self, share_id: str):
        """检测阿里云盘链接有效性"""
        api_url = "https://api.aliyundrive.com/adrive/v3/share_link/get_share_by_anonymous"
        headers = {"Content-Type": "application/json"}
        data = json.dumps({"share_id": share_id})
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.post(api_url, headers=headers, data=data)
                response_json = response.json()
                return bool(response_json.get('has_pwd') or response_json.get('file_infos'))
        except httpx.RequestError as e:
            logger.error(f"检测阿里云盘链接失败: {e}")
            return False

    async def check_115(self, share_id: str):
        """检测115网盘链接有效性"""
        api_url = "https://webapi.115.com/share/snap"
        params = {"share_code": share_id, "receive_code": ""}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(api_url, params=params)
                response_json = response.json()
                return bool(response_json.get('state') or '请输入访问码' in response_json.get('error', ''))
        except httpx.RequestError as e:
            logger.error(f"检测115网盘链接失败: {e}")
            return False

    async def check_quark(self, share_id: str):
        """检测夸克网盘链接有效性"""
        api_url = "https://drive.quark.cn/1/clouddrive/share/sharepage/token"
        headers = {"Content-Type": "application/json"}
        data = json.dumps({"pwd_id": share_id, "passcode": ""})
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.post(api_url, headers=headers, data=data)
                response_json = response.json()
                if response_json.get('message') == "ok":
                    token = response_json.get('data', {}).get('stoken')
                    if token:
                        detail_url = f"https://drive-h.quark.cn/1/clouddrive/share/sharepage/detail?pwd_id={share_id}&stoken={token}&_fetch_share=1"
                        detail_response = await client.get(detail_url)
                        detail_json = detail_response.json()
                        return detail_json.get('data', {}).get('share', {}).get(
                            'status') == 1 or detail_response.status_code == 400
                return response_json.get('message') == "需要提取码"
        except httpx.RequestError as e:
            logger.error(f"检测夸克网盘链接失败: {e}")
            return False

    async def check_123(self, share_id: str):
        """检测123网盘链接有效性"""
        api_url = f"https://www.123pan.com/api/share/info?shareKey={share_id}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(api_url, headers={"User-Agent": "Mozilla/5.0"})
                response_json = response.json()
                if not response_json:
                    return False
                if "分享页面不存在" in response.text or response_json.get('code', -1) != 0:
                    return False
                if response_json.get('data', {}).get('HasPwd', False):
                    return True
                return True
        except (httpx.RequestError, json.JSONDecodeError) as e:
            logger.error(f"检测123网盘链接失败: {e}")
            return False

    async def check_baidu(self, share_id: str):
        """检测百度网盘链接有效性"""
        url = f"https://pan.baidu.com/s/{share_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.get(url, headers=headers, follow_redirects=True)
                text = response.text
                # 无效状态
                if any(x in text for x in
                       ["分享的文件已经被取消", "分享已过期", "你访问的页面不存在", "你所访问的页面"]):
                    return False
                # 需要提取码（有效）
                if "请输入提取码" in text or "提取文件" in text:
                    return True
                # 公开分享（有效）
                if "过期时间" in text or "文件列表" in text:
                    return True
                # 默认未知状态（可能是反爬或异常页面）
                return False
        except httpx.RequestError as e:
            logger.error(f"检测百度网盘链接失败: {e}")
            return False

    async def check_tianyi(self, share_id: str):
        """检测天翼云盘链接有效性"""
        api_url = "https://api.cloud.189.cn/open/share/getShareInfoByCodeV2.action"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                response = await client.post(api_url, data={"shareCode": share_id})
                text = response.text
                if any(x in text for x in ["ShareInfoNotFound", "ShareNotFound", "FileNotFound",
                                           "ShareExpiredError", "ShareAuditNotPass"]):
                    return False
                if "needAccessCode" in text:
                    return True
                return True
        except httpx.RequestError as e:
            logger.error(f"检测天翼云盘链接失败: {e}")
            return False

    async def check_url_with_retry(self, url: str, retries: int = 3):
        """带重试机制的链接检查"""
        for attempt in range(retries):
            try:
                await asyncio.sleep(1)  # 增加1秒的延迟
                share_id, service = self.extract_share_id(url)
                if not share_id or not service:
                    logger.warning(f"无法识别链接: {url}")
                    return False

                check_functions = {
                    "uc": self.check_uc,
                    "aliyun": self.check_aliyun,
                    "quark": self.check_quark,
                    "115": self.check_115,
                    "123": self.check_123,
                    "baidu": self.check_baidu,
                    "tianyi": self.check_tianyi
                }

                if service in check_functions:
                    return await check_functions[service](share_id)

                logger.info(f"暂不支持检测此链接类型: {url}")
                return True
            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次尝试失败: {e}")
                await asyncio.sleep(2)  # 每次重试前等待2秒
        return False  # 重试多次后仍然失败，返回False


async def main():
    # 解析 database.php 文件
    database_php_path = './data/application/database.php'  # 替换为实际路径
    config = parse_database_php(database_php_path)

    # 连接数据库
    conn = connect_to_database(config)
    if not conn:
        return

    # 查询数据库中的 vod_down_url 和 vod_id
    query = "SELECT vod_id, vod_down_url, vod_pic FROM mac_vod"  # 替换为你的查询语句
    results = execute_query(conn, query)

    # 初始化 LinkValidator
    validator = LinkValidator()

    # 校验链接有效性并记录失效的 vod_id、vod_down_url 和 vod_pic
    invalid_records = []
    invalid_vod_ids = []
    invalid_vod_pics = []

    semaphore = Semaphore(10)  # 限制并发数为10

    async def limited_check(row):
        async with semaphore:
            vod_id, vod_down_url, vod_pic = row
            if not await validator.check_url_with_retry(vod_down_url):
                invalid_records.append({
                    "vod_id": vod_id,
                    "vod_down_url": vod_down_url,
                    "vod_pic": vod_pic
                })
                invalid_vod_ids.append(vod_id)
                invalid_vod_pics.append(vod_pic)

    tasks = [limited_check(row) for row in results]
    await asyncio.gather(*tasks)

    # 将无效记录保存到 JSON 文件
    if invalid_records:
        save_invalid_records(invalid_records)

    # 删除数据库中无效的记录和对应的图片文件
    if invalid_vod_ids:
        delete_invalid_records(conn, invalid_vod_ids, invalid_vod_pics)

    # 关闭数据库连接
    conn.close()
    logger.info("数据库连接已关闭")


if __name__ == "__main__":
    logger.info(f"当前工作目录: {os.getcwd()}")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())