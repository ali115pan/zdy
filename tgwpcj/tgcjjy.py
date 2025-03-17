import json
import os
import logging
import re
import asyncio
import httpx
from bs4 import BeautifulSoup
from logging.handlers import RotatingFileHandler
from asyncio import Semaphore

# 配置日志
log_dir = os.path.join(os.getcwd(), 'logs')  # 日志文件目录
os.makedirs(log_dir, exist_ok=True)  # 创建日志目录（如果不存在）
log_file = os.path.join(log_dir, 'link_validator.log')  # 日志文件路径

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
JSON_PATH = os.path.join(os.getcwd(), "mac_vod.json")
INVALID_JSON_PATH = os.path.join(os.getcwd(), "mac_sx.json")

# 图片存储路径
IMAGE_BASE_DIR = os.path.join(os.getcwd(), "data")  # 图片存储的根目录


class LinkValidator:
    def __init__(self, json_path: str, invalid_json_path: str):
        self.json_path = json_path
        self.invalid_json_path = invalid_json_path

    def clear_invalid_json(self):
        """清空无效数据文件（mac_sx.json）"""
        try:
            with open(self.invalid_json_path, 'w', encoding='utf-8') as f:
                json.dump([], f)  # 写入空列表
            logger.info(f"已清空无效数据文件: {self.invalid_json_path}")
        except Exception as e:
            logger.error(f"清空无效数据文件失败: {e}")

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
                    return True

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

    async def load_json_data(self):
        """读取 JSON 文件，若不存在则创建新文件"""
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"加载 JSON 数据: {self.json_path}")
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            logger.error(f"JSON 文件未找到或格式错误: {self.json_path}")
            return []

    async def save_json_data(self, data, path: str):
        """保存数据到 JSON 文件"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"JSON 文件保存成功: {path}")
        except Exception as e:
            logger.error(f"保存 JSON 失败: {e}, 路径: {path}")

    def delete_image_file(self, image_path: str):
        """
        删除本地图片文件
        :param image_path: 图片路径（如 "/upload/vod/tgcj/11624_5953178333204297862_838891039715331091.jpg"）
        """
        if not image_path:
            return

        # 构造完整的文件路径
        full_path = os.path.join(IMAGE_BASE_DIR, image_path.lstrip('/'))
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
                logger.info(f"成功删除图片文件: {full_path}")
            except Exception as e:
                logger.error(f"删除图片文件失败: {full_path}, 错误: {e}")
        else:
            logger.warning(f"图片文件不存在: {full_path}")

    async def validate_links(self):
        """校验链接有效性并分类保存"""
        data = await self.load_json_data()
        valid_data = []
        invalid_data = []

        semaphore = Semaphore(10)  # 限制并发数为10

        async def limited_check(item):
            async with semaphore:
                return await self.check_item_validity(item)

        tasks = [limited_check(item) for item in data]
        results = await asyncio.gather(*tasks)

        for item, is_valid in zip(data, results):
            if is_valid:
                valid_data.append(item)
            else:
                # 如果链接失效，删除对应的图片文件
                image_path = item.get("vod_pic")
                if image_path:
                    self.delete_image_file(image_path)

                invalid_data.append({
                    "vod_id": item.get("vod_id"),
                    "vod_name": item.get("vod_name"),
                    "vod_down_url": item.get("vod_down_url")
                })

        # 保存有效和无效的数据
        await self.save_json_data(valid_data, self.json_path)
        await self.save_json_data(invalid_data, self.invalid_json_path)
        logger.info(f"链接校验完成，有效数据保存到 {self.json_path}，无效数据保存到 {self.invalid_json_path}")

    async def check_item_validity(self, item):
        """检查单个项目的有效性"""
        for url in item.get("vod_down_url", []):
            if not await self.check_url_with_retry(url):
                return False
        return True

    async def run_async(self):
        """异步运行主逻辑"""
        self.clear_invalid_json()  # 清空无效数据文件
        await self.validate_links()

    def run(self):
        """同步启动并运行"""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.run_async())


if __name__ == "__main__":
    logger.info(f"当前工作目录: {os.getcwd()}")
    validator = LinkValidator(JSON_PATH, INVALID_JSON_PATH)
    validator.run()