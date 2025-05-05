import asyncio
import os
import sys
import json
import smtplib
import schedule
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from bilibili_api import user, Credential, exceptions
import re
import traceback
import requests

# --- 配置加载 ---
CONFIG_FILE = "config.json"

def load_config(filename):
    """从JSON文件加载配置。"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            config = json.load(f)
        print(f"配置已从 {filename} 加载。")
        return config
    except FileNotFoundError:
        print(f"错误：配置文件 {filename} 未找到。请确保它与脚本在同一目录下。")
        return None
    except json.JSONDecodeError:
        print(f"错误：配置文件 {filename} 格式无效。请检查 JSON 语法。")
        return None
    except Exception as e:
        print(f"错误：加载配置文件时发生未知错误: {e}")
        return None

# --- 核心函数 ---
async def fetch_user_dynamics(uid, credential=None):
    """获取指定用户的B站动态列表 (第一页)。"""
    mode = "登录模式" if credential else "匿名模式"
    print(f"正在尝试以 {mode} 获取 UID {uid} 的第一页动态...")
    try:
        # 创建User对象，仅在存在时传递credential
        target_user = user.User(uid=uid, credential=credential)

        # 获取动态
        dynamics_page = await asyncio.wait_for(target_user.get_dynamics(offset=0), timeout=30.0)

        if dynamics_page and 'cards' in dynamics_page:
            print(f"成功以 {mode} 获取 UID {uid} 的 {len(dynamics_page['cards'])} 条动态。")
            return dynamics_page
        elif dynamics_page and 'cards' not in dynamics_page:
             print(f"获取到 UID {uid} 的动态数据 ({mode})，但 'cards' 键不存在。响应内容：{dynamics_page}")
             return None
        else:
            print(f"获取 UID {uid} 的动态数据 ({mode}) 为空。")
            return None
    except asyncio.TimeoutError:
        print(f"错误：获取 UID {uid} 动态超时 ({mode})。")
        return None
    except exceptions.ResponseCodeException as e:
        print(f"错误：Bilibili API 返回错误码 {e.code} ({mode}): {e}")
        if e.code == -101 and credential: print("  => 提示：可能是 B站账号未登录或 Cookie 已失效 (在 config.json 中)。")
        elif e.code == -101 and not credential: print("  => 提示：此用户动态可能需要登录才能查看。")
        elif e.code == -412: print("  => 提示：请求被拦截，可能是操作频繁或触发了风控。")
        elif e.code == -352: print("  => 提示：验证失败，可能是 buvid3/csrf 不正确或缺失。")
        elif e.code == 62002: print("  => 提示：目标用户设置了隐私，无法查看动态。")
        return None
    except Exception as e:
        print(f"错误：获取 UID {uid} 动态时 ({mode}) 发生未知错误: {e}")
        traceback.print_exc()
        return None

def sanitize_filename(filename):
    """移除或替换文件名中的无效字符。"""
    filename = filename.replace('/', '-').replace('\\', '-').replace('\0', '')
    invalid_chars = r'[<>:"|?*]'
    filename = re.sub(invalid_chars, '', filename)
    max_len = 100
    if len(filename) > max_len:
        name, ext = os.path.splitext(filename)
        filename = name[:max_len - len(ext)] + ext
    filename = filename.strip(' .')
    if not filename: filename = "untitled"
    return filename

def download_image(url, folder, filename):
    """从URL下载图片到指定文件夹。"""
    filepath = os.path.join(folder, filename)
    if not url.startswith(('http://', 'https://')):
        url = 'http:' + url if url.startswith('//') else 'https://' + url

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                   'Referer': 'https://www.bilibili.com/'}
        print(f"  正在下载图片: {url} -> {filepath}")
        response = requests.get(url, stream=True, timeout=30, headers=headers)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return filepath
    except requests.exceptions.MissingSchema: print(f"  下载图片失败: 无效的URL: {url}"); return None
    except requests.exceptions.HTTPError as e: print(f"  下载图片失败: HTTP错误 {e.response.status_code} for {url}"); return None
    except requests.exceptions.RequestException as e: print(f"  下载图片失败: {url} - {e}"); return None
    except IOError as e: print(f"  保存图片失败: {filepath} - {e}"); return None

def process_dynamics_for_email(dynamics_data, image_dir):
    """处理B站动态数据，筛选含"第N题"的图文动态，下载图片，并返回最新的一题。"""
    if not (dynamics_data and 'cards' in dynamics_data and isinstance(dynamics_data['cards'], list)):
        print("动态数据无效或缺少 'cards' 列表，无法处理。")
        return None

    os.makedirs(image_dir, exist_ok=True)

    items_list = dynamics_data['cards']
    latest_question = None

    for item in items_list:
        dynamic_id_for_error = item.get('desc', {}).get('dynamic_id_str', 'N/A')
        try:
            # 提取dynamic_id
            dynamic_id = item.get('desc', {}).get('dynamic_id_str') or \
                         item.get('display', {}).get('origin', {}).get('dynamic_id_str') or \
                         item.get('desc', {}).get('rid_str') or \
                         item.get('basic', {}).get('comment_id_str')

            if not dynamic_id:
                continue

            card_value = item.get('card')
            if not card_value: continue

            card_data = None
            if isinstance(card_value, str):
                try: card_data = json.loads(card_value)
                except json.JSONDecodeError as e: print(f"  错误：解析 card JSON 失败。ID: {dynamic_id}. Error: {e}"); continue
            elif isinstance(card_value, dict): card_data = card_value
            else: print(f"  警告：item['card'] 类型未知 ({type(card_value)})。跳过。 ID: {dynamic_id}"); continue

            if not card_data: print(f"  内部错误：card_data 为空。跳过。 ID: {dynamic_id}"); continue

            # 获取发布时间
            pub_ts = item.get('desc', {}).get('timestamp') or \
                     card_data.get('item', {}).get('upload_time')

            # 提取内容 (适配多种结构)
            major_module_items = None # 图片字典列表
            description = None
            module_dynamic = card_data.get('modules', {}).get('module_dynamic', {})

            # 结构1: 'modules' -> 'module_dynamic' -> 'major' (draw) and 'desc'
            if module_dynamic:
                major_data = module_dynamic.get('major', {})
                desc_data = module_dynamic.get('desc')
                if major_data.get('type') == 'MAJOR_TYPE_DRAW':
                     draw_data = major_data.get('draw')
                     if draw_data and 'items' in draw_data and desc_data and 'text' in desc_data:
                         major_module_items = draw_data['items']
                         description = desc_data['text']

            # 结构2: 直接 'item' 带有 'pictures' 和 'description' (旧格式或简单格式)
            if major_module_items is None:
                item_data = card_data.get('item', {})
                if isinstance(item_data.get('pictures'), list) and item_data.get('description'):
                    major_module_items = [{'src': pic.get('img_src')} for pic in item_data['pictures'] if pic.get('img_src')]
                    description = item_data['description']

            # 结构3: 转发动态的原始项目
            if major_module_items is None and 'origin' in card_data:
                 origin_card_value = card_data.get('origin')
                 origin_card_data = None
                 if isinstance(origin_card_value, str):
                     try: origin_card_data = json.loads(origin_card_value)
                     except json.JSONDecodeError: pass
                 elif isinstance(origin_card_value, dict): origin_card_data = origin_card_value

                 if origin_card_data:
                    origin_item_data = origin_card_data.get('item', {})
                    if isinstance(origin_item_data.get('pictures'), list) and origin_item_data.get('description'):
                        major_module_items = [{'src': pic.get('img_src')} for pic in origin_item_data['pictures'] if pic.get('img_src')]
                        description = origin_item_data['description']

            if major_module_items is None or description is None:
                 continue

            # 核心筛选和信息提取
            text_content = description.strip()
            if not text_content: continue

            # 严格筛选: 必须包含 "第 N 题"
            question_match = re.search(r"第\s*(\d+)\s*题", text_content, re.IGNORECASE)
            if not question_match:
                continue
            question_number = question_match.group(1) # 提取题号 N
            print(f"  匹配到 '第 {question_number} 题', 处理中... ID: {dynamic_id}")

            # 生成标题
            title = f"每日一题 | 第 {question_number} 题"

            # 格式化日期和时间字符串
            pub_time_str = "未知时间"
            formatted_date_for_filename = "nodate"
            if pub_ts:
                try:
                    dt_object = datetime.fromtimestamp(int(pub_ts))
                    pub_time_str = dt_object.strftime('%Y-%m-%d %H:%M')
                    formatted_date_for_filename = dt_object.strftime('%Y_%m_%d')
                except Exception as e:
                    print(f"    解析时间戳失败: {pub_ts}, 错误: {e}")

            # 提取图片 URL (第一张)
            if not (isinstance(major_module_items, list) and len(major_module_items) > 0):
                 print(f"  跳过：图片列表为空或无效。 ID: {dynamic_id}"); continue

            image_info = major_module_items[0]
            image_url = image_info.get('src')
            if not image_url:
                print(f"  跳过：无法获取图片 URL (检查 'src' key)。 ID: {dynamic_id}"); continue

            # 下载图片
            _, ext = os.path.splitext(image_url.split('?')[0])
            if not ext or len(ext) > 6: ext = '.jpg'
            image_filename = sanitize_filename(f"{question_number}_{formatted_date_for_filename}{ext}")
            local_image_path = download_image(image_url, image_dir, image_filename)

            if not local_image_path:
                print(f"  处理失败：图片下载失败。跳过此动态。 ID: {dynamic_id}")
                continue

            # 如果是第一个匹配的题目，保存为最新题目
            if latest_question is None:
                latest_question = {
                    'title': title,
                    'text': text_content,
                    'image_path': local_image_path,
                    'pub_time': pub_time_str,
                    'question_number': question_number
                }
                print(f"  找到最新题目：第 {question_number} 题")
                break  # 只需要最新的一题

        except Exception as e:
            print(f"  处理动态时发生意外错误：{e}. Dynamic ID: {dynamic_id_for_error}")
            traceback.print_exc()
            continue

    return latest_question

def send_email(email_config, question_data):
    """发送包含每日一题的邮件"""
    if not question_data:
        print("没有找到可发送的题目数据")
        return False
    
    try:
        # 创建邮件
        msg = MIMEMultipart()
        msg['From'] = email_config['sender']
        msg['To'] = email_config['receiver']
        msg['Subject'] = f"B站每日一题 - {question_data['title']}"
        
        # 邮件正文
        email_body = f"""
        <html>
        <body>
            <h2>{question_data['title']} ({question_data['pub_time']})</h2>
            <p><b>题目内容:</b></p>
            <p>{question_data['text']}</p>
            <p><img src="cid:question_image" width="80%"></p>
        </body>
        </html>
        """
        msg.attach(MIMEText(email_body, 'html'))
        
        # 添加图片附件
        with open(question_data['image_path'], 'rb') as img_file:
            img = MIMEImage(img_file.read())
            img.add_header('Content-ID', '<question_image>')
            msg.attach(img)
        
        # 连接到SMTP服务器并发送
        with smtplib.SMTP_SSL(email_config['smtp_server'], email_config['smtp_port']) as server:
            server.login(email_config['sender'], email_config['password'])
            server.send_message(msg)
        
        print(f"成功发送每日一题邮件：第 {question_data['question_number']} 题")
        return True
    except Exception as e:
        print(f"发送邮件时发生错误: {e}")
        traceback.print_exc()
        return False

def job():
    """定时任务：获取并发送每日一题"""
    print(f"\n--- 开始执行定时任务 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ---")
    
    # 加载配置
    config = load_config(CONFIG_FILE)
    if not config:
        print("无法加载配置，任务终止")
        return
    
    # 获取配置值
    TARGET_UID = config.get("TARGET_UID")
    IMAGE_DIR = config.get("IMAGE_DIR", "bili_images")
    CREDS_CONFIG = config.get("CREDENTIALS", {})
    EMAIL_CONFIG = config.get("EMAIL", {})
    
    if not TARGET_UID:
        print(f"错误: 配置文件 {CONFIG_FILE} 中缺少 TARGET_UID。")
        return
    
    if not EMAIL_CONFIG or not all(k in EMAIL_CONFIG for k in ['sender', 'password', 'receiver', 'smtp_server', 'smtp_port']):
        print("错误: 邮件配置不完整，请检查config.json中的EMAIL部分")
        return
    
    print(f"目标用户 UID: {TARGET_UID}")
    print(f"图片保存目录: {IMAGE_DIR}")
    
    # 尝试使用登录模式
    credential = None
    SESSDATA = CREDS_CONFIG.get("SESSDATA")
    BILI_JCT = CREDS_CONFIG.get("BILI_JCT")
    BUVID3 = CREDS_CONFIG.get("BUVID3")
    DEDEUSERID = CREDS_CONFIG.get("DEDEUSERID")
    
    if SESSDATA and BILI_JCT and BUVID3:
        print("正在使用 config.json 中的 Cookie 信息创建凭据...")
        try:
            dedeuserid_val = DEDEUSERID if DEDEUSERID and DEDEUSERID.strip() else None
            credential = Credential(sessdata=SESSDATA, bili_jct=BILI_JCT, buvid3=BUVID3, dedeuserid=dedeuserid_val)
            print("凭据创建成功。")
        except Exception as e:
            print(f"错误：创建 Credential 对象失败：{e}")
            print("将尝试切换回匿名模式。")
            credential = None
    else:
        print("使用匿名模式获取动态")
    
    # 获取动态
    dynamics_data = asyncio.run(fetch_user_dynamics(TARGET_UID, credential))
    
    # 处理数据并发送邮件
    if dynamics_data:
        print("\n开始处理动态数据...")
        latest_question = process_dynamics_for_email(dynamics_data, IMAGE_DIR)
        
        if latest_question:
            send_email(EMAIL_CONFIG, latest_question)
        else:
            print("未找到符合条件的每日一题")
    else:
        print("\n未能成功获取动态数据，任务终止。")

# --- 主执行块 ---
if __name__ == "__main__":
    print("--- B站每日一题邮件发送工具 ---")
    
    # 加载配置
    config = load_config(CONFIG_FILE)
    if not config:
        sys.exit(1)
    
    # 检查邮件配置
    if "EMAIL" not in config or not all(k in config["EMAIL"] for k in ['sender', 'password', 'receiver', 'smtp_server', 'smtp_port']):
        print("错误: 邮件配置不完整，请在config.json中添加EMAIL部分，包含sender、password、receiver、smtp_server和smtp_port字段")
        sys.exit(1)
    
    # 首次运行立即执行一次
    print("首次运行，立即执行一次任务...")
    job()
    
    # 设置定时任务，每天8:10执行
    schedule.every().day.at("08:10").do(job)
    print("已设置定时任务，将在每天 08:10 执行")
    
    # 运行定时任务循环
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"\n程序发生错误: {e}")
        traceback.print_exc()