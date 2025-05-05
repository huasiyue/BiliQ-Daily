import asyncio
import os
import requests
from datetime import datetime
from bilibili_api import user, Credential, exceptions
import re
import sys
import json
import traceback

# --- Configuration Loading ---
CONFIG_FILE = "config.json"

def load_config(filename):
    """Loads configuration from a JSON file."""
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

# --- Core Functions (Modified fetch_user_dynamics) ---

async def fetch_user_dynamics(uid, credential=None):
    """
    获取指定用户的B站动态列表 (第一页)。
    根据是否提供 credential 决定使用登录模式还是匿名模式。
    """
    mode = "登录模式" if credential else "匿名模式"
    print(f"正在尝试以 {mode} 获取 UID {uid} 的第一页动态...")
    try:
        # Create User object, passing credential only if it exists
        target_user = user.User(uid=uid, credential=credential)

        # Fetch dynamics
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
    """Removes or replaces characters invalid in filenames."""
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
    """Downloads an image from a URL to a specified folder."""
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

# ==============================================================================
# MODIFIED FUNCTION: process_dynamics_to_markdown (No significant changes needed here)
# ==============================================================================
def process_dynamics_to_markdown(dynamics_data, output_md_file, image_dir):
    """
    处理B站动态数据，严格筛选含“第N题”的图文动态，下载图片(命名为 N_YYYY_MM_DD)，
    并生成/更新 Markdown 文件。
    """
    if not (dynamics_data and 'cards' in dynamics_data and isinstance(dynamics_data['cards'], list)):
        print("动态数据无效或缺少 'cards' 列表，无法处理。")
        return

    os.makedirs(image_dir, exist_ok=True)

    existing_content = ""
    processed_ids = set()
    if os.path.exists(output_md_file):
        try:
            with open(output_md_file, 'r', encoding='utf-8') as f:
                existing_content = f.read()
                # Use a more robust regex to find IDs, allowing for variations
                processed_ids = set(re.findall(r"dynamic_id(?:_str)?:\s*(\d+)", existing_content, re.IGNORECASE)) \
                                | set(re.findall(r"<!--\s*ID:\s*(\d+)\s*-->", existing_content)) # Add comment-based ID tracking
                print(f"找到 {len(processed_ids)} 个可能已处理的动态 ID。")
        except Exception as e:
            print(f"警告：读取现有 Markdown 文件 {output_md_file} 失败: {e}")

    new_markdown_entries = []
    items_list = dynamics_data['cards']

    for item in items_list:
        dynamic_id_for_error = item.get('desc', {}).get('dynamic_id_str', 'N/A')
        try:
            # --- Extract dynamic_id early and check if already processed ---
            dynamic_id = item.get('desc', {}).get('dynamic_id_str') or \
                         item.get('display', {}).get('origin', {}).get('dynamic_id_str') or \
                         item.get('desc', {}).get('rid_str') or \
                         item.get('basic', {}).get('comment_id_str') # Add more potential ID sources

            if not dynamic_id:
                # print(f"  警告: 无法为某个卡片提取 dynamic_id，跳过。卡片内容片段: {str(item)[:200]}") # Debugging if needed
                continue # Cannot reliably check if processed

            if dynamic_id in processed_ids:
                # print(f"  跳过：动态 ID {dynamic_id} 已处理过。") # Reduce noise
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

            # --- 获取发布时间 ---
            pub_ts = item.get('desc', {}).get('timestamp') or \
                     card_data.get('item', {}).get('upload_time') # Check item structure too

            # --- 提取内容 (适配多种结构) ---
            major_module_items = None # list of image dicts
            description = None
            module_dynamic = card_data.get('modules', {}).get('module_dynamic', {})

            # Structure 1: 'modules' -> 'module_dynamic' -> 'major' (draw) and 'desc'
            if module_dynamic:
                major_data = module_dynamic.get('major', {})
                desc_data = module_dynamic.get('desc')
                if major_data.get('type') == 'MAJOR_TYPE_DRAW':
                     draw_data = major_data.get('draw')
                     if draw_data and 'items' in draw_data and desc_data and 'text' in desc_data:
                         major_module_items = draw_data['items']
                         description = desc_data['text']
                         # print(f"  找到图文内容 (结构 'modules'): ID {dynamic_id}")

            # Structure 2: Direct 'item' with 'pictures' and 'description' (Older or simpler format)
            if major_module_items is None:
                item_data = card_data.get('item', {})
                if isinstance(item_data.get('pictures'), list) and item_data.get('description'):
                    # Map 'pictures' structure to the same format as 'items' if possible
                    major_module_items = [{'src': pic.get('img_src')} for pic in item_data['pictures'] if pic.get('img_src')]
                    description = item_data['description']
                    # print(f"  找到图文内容 (结构 'item'): ID {dynamic_id}")

            # Structure 3: Origin item for forwarded dynamics (less likely for "每日一题")
            if major_module_items is None and 'origin' in card_data:
                 origin_card_value = card_data.get('origin')
                 origin_card_data = None
                 if isinstance(origin_card_value, str):
                     try: origin_card_data = json.loads(origin_card_value)
                     except json.JSONDecodeError: pass # Ignore parse error here
                 elif isinstance(origin_card_value, dict): origin_card_data = origin_card_value

                 if origin_card_data:
                    origin_item_data = origin_card_data.get('item', {})
                    if isinstance(origin_item_data.get('pictures'), list) and origin_item_data.get('description'):
                        major_module_items = [{'src': pic.get('img_src')} for pic in origin_item_data['pictures'] if pic.get('img_src')]
                        description = origin_item_data['description'] # Use original description

            if major_module_items is None or description is None:
                 # print(f"  跳过：动态 ID {dynamic_id} 未找到有效的图文内容结构。")
                 continue # Skip if no usable text/image content found

            # --- 核心筛选和信息提取 ---
            text_content = description.strip()
            if not text_content: continue # Skip if text is empty

            # 1. *** 严格筛选: 必须包含 "第 N 题" ***
            question_match = re.search(r"第\s*(\d+)\s*题", text_content, re.IGNORECASE)
            if not question_match:
                # print(f"  跳过：内容未匹配 '第 N 题'。 ID: {dynamic_id}") # Reduce noise unless debugging
                continue
            question_number = question_match.group(1) # 提取题号 N
            print(f"  匹配到 '第 {question_number} 题', 处理中... ID: {dynamic_id}")

            # 2. 生成标题
            title = f"每日一题 | 第 {question_number} 题"

            # 3. 格式化日期 (用于文件名) 和时间字符串
            pub_time_str = "未知时间"
            formatted_date_for_filename = "nodate"
            if pub_ts:
                try:
                    dt_object = datetime.fromtimestamp(int(pub_ts))
                    pub_time_str = dt_object.strftime('%Y-%m-%d %H:%M')
                    formatted_date_for_filename = dt_object.strftime('%Y_%m_%d') # YYYY_MM_DD
                except Exception as e:
                    print(f"    解析时间戳失败: {pub_ts}, 错误: {e}")

            # 4. 提取图片 URL (第一张)
            if not (isinstance(major_module_items, list) and len(major_module_items) > 0):
                 print(f"  跳过：图片列表为空或无效。 ID: {dynamic_id}"); continue

            image_info = major_module_items[0] # Take the first image structure
            image_url = image_info.get('src') # Prefer 'src' key
            if not image_url:
                # Fallback for potential different key names if needed
                # image_url = image_info.get('img_src') # Example fallback
                print(f"  跳过：无法获取图片 URL (检查 'src' key)。 ID: {dynamic_id}"); continue


            # 5. *** 下载图片 (新命名: N_YYYY_MM_DD) ***
            _, ext = os.path.splitext(image_url.split('?')[0])
            if not ext or len(ext) > 6: ext = '.jpg' # Default to jpg, allow slightly longer extensions like .jpeg
            # 使用提取的题号和格式化日期进行命名
            image_filename = sanitize_filename(f"{question_number}_{formatted_date_for_filename}{ext}")
            local_image_path = download_image(image_url, image_dir, image_filename)

            if not local_image_path:
                print(f"  处理失败：图片下载失败。跳过此动态。 ID: {dynamic_id}")
                continue

            # 6. 格式化 Markdown 条目
            relative_image_path = os.path.join(image_dir, image_filename).replace('\\', '/')
            # Add a comment with the dynamic ID for easier tracking/debugging
            markdown_entry = f"""<!-- ID: {dynamic_id} -->
## {title} ({pub_time_str})

**文本:**

{text_content}

**图片:**

![{title}]({relative_image_path})

---
"""
            new_markdown_entries.append(markdown_entry)
            if dynamic_id: processed_ids.add(dynamic_id) # Ensure ID is added after successful processing
            print(f"  成功处理并格式化动态 ID: {dynamic_id}")

        except Exception as e:
            print(f"  处理动态时发生意外错误：{e}. Dynamic ID: {dynamic_id_for_error}")
            traceback.print_exc()
            continue

    if new_markdown_entries:
        # Prepend new entries to the existing content
        final_content = "\n".join(new_markdown_entries) + "\n" + existing_content
        try:
            with open(output_md_file, 'w', encoding='utf-8') as f: f.write(final_content)
            print(f"\n成功将 {len(new_markdown_entries)} 条新【每日一题】动态写入到 {output_md_file}")
        except IOError as e: print(f"\n错误：写入 Markdown 文件 {output_md_file} 失败: {e}")
    else:
        print("\n没有找到新的符合【每日一题】条件的动态。")

# --- Main Execution Block ---
if __name__ == "__main__":
    print("--- Bilibili 动态 Markdown 生成器 (每日一题筛选版) ---")

    # Load configuration
    config = load_config(CONFIG_FILE)
    if not config:
        sys.exit(1)

    # Get config values or use defaults
    TARGET_UID = config.get("TARGET_UID")
    OUTPUT_MD_FILE = config.get("OUTPUT_MD_FILE", "bilibili_dynamics.md")
    IMAGE_DIR = config.get("IMAGE_DIR", "bili_images")
    CREDS_CONFIG = config.get("CREDENTIALS", {})

    if not TARGET_UID:
        print(f"错误: 配置文件 {CONFIG_FILE} 中缺少 TARGET_UID。")
        sys.exit(1)

    print(f"目标用户 UID: {TARGET_UID}")
    print(f"输出 Markdown 文件: {OUTPUT_MD_FILE}")
    print(f"图片保存目录: {IMAGE_DIR}")

    # Determine login mode from command line argument
    use_login = False
    if len(sys.argv) > 1 and sys.argv[1] == '1':
        use_login = True
        print("\n请求使用登录模式 (命令行参数 '1').")
    else:
        print("\n将使用匿名模式 (默认或命令行参数 '0').")
        print("注意：匿名模式可能受限，或无法获取需要登录才能查看的动态。")

    credential = None
    if use_login:
        SESSDATA = CREDS_CONFIG.get("SESSDATA")
        BILI_JCT = CREDS_CONFIG.get("BILI_JCT")
        BUVID3 = CREDS_CONFIG.get("BUVID3")
        DEDEUSERID = CREDS_CONFIG.get("DEDEUSERID") # Optional

        if not SESSDATA or not BILI_JCT or not BUVID3:
            print("\n错误：请求了登录模式，但 config.json 中 CREDENTIALS 部分缺少必要的 SESSDATA, BILI_JCT 或 BUVID3。")
            print("将尝试切换回匿名模式。")
            use_login = False # Fallback to anonymous
        else:
            print("正在使用 config.json 中的 Cookie 信息创建凭据...")
            try:
                 dedeuserid_val = DEDEUSERID if DEDEUSERID and DEDEUSERID.strip() else None
                 credential = Credential(sessdata=SESSDATA, bili_jct=BILI_JCT, buvid3=BUVID3, dedeuserid=dedeuserid_val)
                 print("凭据创建成功。")
            except Exception as e:
                 print(f"错误：创建 Credential 对象失败：{e}")
                 print("将尝试切换回匿名模式。")
                 use_login = False # Fallback to anonymous
                 credential = None # Ensure credential is None

    # Fetch dynamics (passing credential which might be None)
    dynamics_data = asyncio.run(fetch_user_dynamics(TARGET_UID, credential))

    # Process data if fetched successfully
    if dynamics_data:
        print("\n开始处理动态数据并生成 Markdown...")
        process_dynamics_to_markdown(dynamics_data, OUTPUT_MD_FILE, IMAGE_DIR)
        print("\n--- 处理完成 ---")
    else:
        print("\n未能成功获取动态数据，程序退出。请检查：")
        print(f"1. 网络连接是否正常。")
        if use_login: print("2. config.json 中的 Cookie (SESSDATA, bili_jct, buvid3) 是否仍然有效且未过期。")
        else: print("2. 目标用户的动态是否公开可见，或是否需要登录查看。")
        print(f"3. 目标用户 UID ({TARGET_UID}) 是否正确。")
        print(f"4. 是否触发了 B站的风控策略 (如请求过于频繁)。")
        sys.exit(1)

