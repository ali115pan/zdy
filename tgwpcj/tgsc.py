# -*- coding: utf-8 -*-
import requests
import json
import os
import sys
import yaml
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 初始化日志配置
script_dir = os.path.dirname(os.path.abspath(__file__))  # 脚本所在目录
log_dir = os.path.join(script_dir, 'logs')  # 日志文件目录
os.makedirs(log_dir, exist_ok=True)  # 创建日志目录（如果不存在）
log_file = os.path.join(log_dir, 'tgsc.log')  # 日志文件路径

# 设置日志处理器
log_handler = RotatingFileHandler(
    log_file,
    maxBytes=10 * 1024 * 1024,  # 每个日志文件最大 10MB
    backupCount=5,  # 最多保留 5 个日志文件
    encoding='utf-8'
)

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[log_handler]
)
logger = logging.getLogger(__name__)

# 初始化设置
session = requests.Session()
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36'
}
globals_dict = {}
success_num = 0
error_num = 0

# 获取分类ID函数
def get_category_id(category_name):
    """
    根据分类名称获取分类ID
    """
    category_map = {
        '综艺': 20,
        '国产剧': 21,
        '韩日泰': 22,
        '欧美剧': 23,
        '夸克盘': 24,
        'uc盘': 25,
        '115盘': 26,
        'UC盘': 25,
        '运营商': 27,
        '123盘': 28,
        '阿里盘': 29,
        '动漫': 30,
        '网盘': 31
    }
    # 大小写不敏感匹配
    for key, value in category_map.items():
        if key.lower() == category_name.lower():
            return value
    logger.warning(f"分类 '{category_name}' 不存在于映射表中")
    return None

# 数据上传函数
def post_data(data):
    """
    上传数据到服务器
    """
    global success_num, error_num, globals_dict
    data_url = f"{domain_url}/api.php/autotasks/update_data"
    for v in data['list']:
        v['pass'] = Apipass
        v['param'] = json.dumps(globals_dict)
        
        # 删除旧的 type_id（如果存在）
        if 'type_id' in v:
            del v['type_id']
        
        # 获取分类ID
        type_name = v.get('type_name', '')
        type_id = get_category_id(type_name)
        if type_id is None:
            logger.warning(f"分类 '{type_name}' 不存在，跳过上传")
            error_num += 1
            continue
        v['type_id'] = type_id
        
        # 设置默认下载来源
        v['vod_down_url'] = v.get('vod_down_url', '')
        v['vod_down_from'] = 'BJ'
        
        try:
            # 发送POST请求
            response = session.post(data_url, data=v, headers=headers, timeout=30)
            ret = response.json()
            log_msg = (
                f"{globals_dict['des']} 第{data['page']}页\n"
                f"视频名称：{v['vod_name']} {v['vod_remarks']}\n"
                f"分类名称：{v['type_name']} (ID: {v['type_id']})\n"
                f"入库提示：{ret['msg']}\n"
            )
            if "ok" in ret.get("msg"):
                success_num += 1
            elif ret.get("code") > 3000:
                logger.error(f"发布入库失败，请根据提示做检查：{ret['msg']}")
                os._exit(1)
            else:
                error_num += 1
            logger.info(log_msg)
        except requests.exceptions.RequestException as e:
            error_num += 1
            logger.error(f"{globals_dict['des']}\nPOST请求入库失败，错误内容: \n{e}")

# 本地文件处理函数
def process_local_file(file_path):
    """
    处理本地文件并返回格式化后的数据
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
        
        formatted_data = {
            "code": 1,
            "msg": "本地文件数据",
            "page": 1,
            "pagecount": 1,
            "limit": len(local_data),
            "total": len(local_data),
            "list": []
        }
        for item in local_data:
            formatted_item = {
                "vod_name": item.get("vod_name", "未命名"),
                "vod_remarks": item.get("vod_remarks", ""),
                "type_name": item.get("type_name", "网盘"),
                "vod_pic": item.get("vod_pic", ""),
                "vod_content": item.get("vod_content", ""),
                "vod_actor": item.get("vod_actor", ""),
                "vod_director": item.get("vod_director", ""),
                "vod_year": item.get("vod_year", ""),
                "vod_area": item.get("vod_area", ""),
                "vod_down_url": item.get("vod_down_url", ""),
                "vod_down_from": "BJ",
                "pass": Apipass,
                "vod_year": item.get("vod_year", ""),
                "param": json.dumps(globals_dict)
            }
            if 'vod_play_url' in item:
                formatted_item.update({
                    'vod_play_from': '$$$'.join(item.get('vod_play_from', [''])),
                    'vod_play_url': '$$$'.join(item.get('vod_play_url', [''])),
                    'vod_play_server': '$$$'.join(item.get('vod_play_server', [''])),
                    'vod_play_note': '$$$'.join(item.get('vod_play_note', ['']))
                })
            if 'vod_down_url' in item:
                formatted_item['vod_down_url'] = '$$$'.join(item.get('vod_down_url', ['']))
            formatted_data["list"].append(formatted_item)
        return formatted_data
    except Exception as e:
        logger.error(f"本地文件处理失败: {str(e)}")
        return None

if __name__ == "__main__":
    logger.info(f"当前Python 版本：{sys.version}")
    current_dir = Path(__file__).parent
    config_path = current_dir / 'tgsc.yaml'
    
    if not config_path.exists():
        logger.error("配置文件不存在，请检查路径。")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as ff:
        try:
            datas = yaml.safe_load(ff)
        except yaml.YAMLError as exc:
            logger.error(f"读取配置错误：{exc}")
            sys.exit(1)

    if datas is not None:
        domain_url = datas.get('domain_url')
        token = datas.get('token')
        Apipass = datas.get('Apipass')
        local_file_path = datas.get('local_file_path')

        if local_file_path and Path(local_file_path).exists():
            logger.info("\n🔍 检测到内容更新，进入更新模式...")
            local_data = process_local_file(local_file_path)
            if local_data:
                globals_dict = {"des": "内容更新任务"}
                post_data(local_data)
                logger.info(f"\n✅ 内容更新任务成功：{success_num}条，失败：{error_num}条")
                # 清空本地文件
                # Path(local_file_path).unlink()
                # logger.info(f"已清空本地文件：{local_file_path}")
            else:
                logger.error("\n❌ 文件处理失败，请检查：1.文件格式 2.字段匹配")
            sys.exit(0)

    now_time = time.strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f"\n当前时间：{now_time}")