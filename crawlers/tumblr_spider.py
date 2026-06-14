"""
Tumblr 社交媒体爬虫
=====================

功能概述：
    使用 Selenium (undetected-chromedriver) 抓取 Tumblr 平台的帖子。
    按关键词搜索帖子，向下滚动加载数据，保存帖子到数据库。

技术方案：
    1. 使用 uc.Chrome 模拟浏览器访问 Tumblr 搜索页
    2. 等待页面主体渲染，并模拟人类滚动以加载异步生成的帖子区块
    3. 解析 <article> 标签并深层榨取文本与帖子原链接
    4. 将获取到的数据统一交由 save_social_post 进行去重并保存至数据库

与 Bluesky 方式的区别：
    - Tumblr 官方无直接暴露的免鉴权搜索 API，采用模拟浏览器方式抓取
    - 抓取速度依赖于页面渲染和滚动加载时长
    - 不需要单独抓取评论树

关键词: china, taiwan
每个关键词最多爬取 DEFAULT_MAX_PER_KW 条帖子
"""
import os
import sys
import uuid
import hashlib
import argparse
import time
import random
import datetime

# ============================================================
# 代理配置初始化（必须在引入 uc 和 requests 之前设置环境变量）
# ============================================================
from proxy_config import PROXIES
# 注意：不设置系统代理环境变量，通过 Chrome --proxy-server 设置
# 避免 Selenium 驱动内部通信走代理导致死锁

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import shutil

from common_db import get_db, save_social_post, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
from crawler_config import DB


# ============================================================
# 配置常量
# ============================================================

# 搜索关键词列表
from crawler_config import ALL_KEYWORDS  # 使用统一关键词列表
# 每个关键词最多保存的帖子数
DEFAULT_MAX_PER_KW = 5
# 定义 Tumblr 专属的浏览器缓存路径，防止每次均需重新配置浏览器
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_chrome_profile_tumblr")


# ============================================================
# 文本处理工具
# ============================================================

def clean_text(text):
    """
    清理并格式化提取出的文本。
    
    参数:
        text (str): 原始文本
        
    返回值:
        str: 替换换行与回车后的纯文本
    """
    if not text: return ""
    return str(text).replace("\n", " ").replace("\r", " ").strip()


# ============================================================
# 爬虫主流程
# ============================================================

def crawl(keywords, max_per_kw):
    """
    爬虫主流程：使用 Selenium 搜索并抓取 Tumblr 帖子。

    流程说明：
      1. 配置 undetected_chromedriver 并加载本地代理
      2. 遍历每个关键词，访问 Tumblr 搜索 URL
      3. 模拟滚动，触发瀑布流加载
      4. 提取 <article> 区块，提取正文文本与链接
      5. 调用统一的 save_social_post 入库

    参数:
        keywords (list[str]): 关键词列表
        max_per_kw (int): 每个关键词最多保存的帖子数

    返回值:
        tuple: (items_found, items_new, items_updated)
    """
    print("=== Tumblr 爬虫 ===")
    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
    
    # 浏览器配置
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    # 清理掉 HTTP_PROXY 等协议头，直接提取 "IP:PORT" 传给 Chrome
    proxy_server = PROXIES["http"].replace("http://", "")
    chrome_options.add_argument(f'--proxy-server={proxy_server}')
    chrome_options.add_argument("--lang=zh-CN,zh;q=0.9")

    chromedriver_path = shutil.which('chromedriver') or '/usr/bin/chromedriver'
    service = Service(chromedriver_path)
    driver = None
    conn = get_db()
    cur = conn.cursor()
    
    items_found = 0
    items_new = 0
    items_updated = 0

    try:
        print("  🚀 正在静默拉起浏览器...")
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # 卸载系统代理环境变量，防止 Chrome 内部通信死锁
        for env_key in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
            if env_key in os.environ: 
                del os.environ[env_key]

        for kw in keywords:
            print(f"\n--- 搜索: {kw} ---")
            url = f"https://www.tumblr.com/search/{kw}"
            
            try:
                driver.get(url)
                
                # 🎯 防御白屏：等待网页的基础框架加载完毕
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                except Exception:
                    print(f"    [WARN] 页面渲染缓慢，可能遇到了网络拦截。")
                
                time.sleep(random.uniform(5.0, 8.0)) 
                
                # 模拟向下滚动
                scroll_times = 3
                for i in range(scroll_times):
                    print(f"    [INFO] 正在向下滚动页面以加载更多动态 ({i+1}/{scroll_times})...")
                    try:
                        safe_scroll_js = """
                            var h = document.documentElement.scrollHeight || (document.body ? document.body.scrollHeight : 0);
                            window.scrollTo(0, h);
                        """
                        driver.execute_script(safe_scroll_js)
                    except Exception:
                        pass
                    time.sleep(random.uniform(3.0, 6.0)) 
                
                # 提取帖子区块
                articles = driver.find_elements(By.TAG_NAME, 'article')
                print(f"    [INFO] 发现 {len(articles)} 个潜在帖子区块，开始提取文本...")
                
                cnt = 0
                for article in articles:
                    if cnt >= max_per_kw: 
                        break
                    items_found += 1
                    
                    try:
                        # 尝试深层榨取文本
                        text_elements = article.find_elements(By.XPATH, ".//p | .//span | .//h2")
                        items = []
                        for el in text_elements:
                            txt = el.text.strip()
                            if len(txt) > 2: 
                                items.append(txt)
                                
                        if not items:
                            raw_text = article.text.strip()
                            if len(raw_text) > 5:
                                items = [raw_text]
                            else:
                                continue
                                
                        unique_items = list(dict.fromkeys(items))
                        full_text = '\n'.join(unique_items)
                        
                        # 提取链接 
                        post_url = f"https://www.tumblr.com/search/{kw}"
                        try:
                            a_tags = article.find_elements(By.TAG_NAME, "a")
                            for a in a_tags:
                                href = a.get_attribute("href")
                                if href and ("/post/" in href or ".tumblr.com/" in href): 
                                    post_url = href
                                    break
                        except Exception:
                            pass
                        
                        # Tumblr 无法直接通过 API 获取稳定 ID，使用原帖链接生成的 MD5 哈希作为帖子 ID 去重
                        post_id = hashlib.md5(post_url.encode()).hexdigest()
                        
                        # 构造数据体并交由统一函数入库
                        post_data = {
                            "uuid": str(uuid.uuid4()),
                            "site_name": "Tumblr",
                            "trigger_keyword": kw,
                            "source_board": "search",
                            "post_id": post_id,
                            "title": clean_text(full_text)[:100],  # 取前100个字符做标题
                            "author": "Tumblr User",
                            "publish_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            "like_count": 0,
                            "comment_count": 0,
                            "content": full_text,
                            "original_url": post_url,
                            "image_url": ""
                        }
                        
                        # 保存帖子数据 (使用 INSERT ... ON DUPLICATE KEY UPDATE)
                        is_new, is_updated = save_social_post(cur, post_data)
                        conn.commit()
                        
                        if is_new:
                            items_new += 1
                            print(f"  [NEW] 提取成功: {clean_text(full_text)[:60]}...")
                        if is_updated:
                            items_updated += 1
                            print(f"  [UPDATE] 更新数据: {clean_text(full_text)[:60]}...")
                            
                        cnt += 1
                        
                    except Exception as e:
                        print(f"  [ERR] 解析单个区块发生异常: {e}")
                        continue 
                        
            except Exception as e:
                print(f"  [ERR] 检索关键词 【{kw}】 时发生异常: {e}")
                
            time.sleep(random.uniform(4.0, 7.0))
            print(f"  {kw}: {cnt}条")
            
    finally: 
        if driver:
            try: driver.quit()
            except Exception: pass
        cur.close()
        conn.close()
        
    print("=== 完成 ===")
    return items_found, items_new, items_updated


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。

    参数说明:
        --config-id: 爬取配置ID（由调度系统传入）
        --keyword:   指定单个关键词（覆盖默认 ALL_KEYWORDS）
        --max:       每个关键词最多爬取的帖子数（覆盖 DEFAULT_MAX_PER_KW）
        --log-id:    爬取日志ID（用于更新日志状态）
        max_legacy:  位置参数（向后兼容旧版本）

    返回值:
        argparse.Namespace: 解析后的参数对象
    """
    parser = argparse.ArgumentParser(description="Tumblr Spider")
    parser.add_argument("--config-id", type=int, default=None, help="crawl_config ID")
    parser.add_argument("--keyword", type=str, default=None, help="Keyword to crawl (overrides default list)")
    parser.add_argument("--max", type=int, default=None, help="Max results per keyword (overrides default)")
    parser.add_argument("--log-id", type=int, default=None, help="crawl_log ID to update")
    # 向后兼容：允许位置参数作为 max_per_kw
    parser.add_argument("max_legacy", nargs="?", type=int, default=None, help="(legacy) max per keyword")
    return parser.parse_args()

def main():
    """
    主函数：解析参数 → 执行爬虫 → 更新日志。
    """
    args = parse_args()

    # 确定关键词列表
    if args.keyword:
        keywords = [k.strip() for k in args.keyword.split(",") if k.strip()]
    else:
        keywords = ALL_KEYWORDS

    # 确定每个关键词最多爬取的帖子数（优先 --max，其次 --max_legacy，最后默认值）
    max_per_kw = args.max if args.max is not None else (args.max_legacy if args.max_legacy is not None else DEFAULT_MAX_PER_KW)

    config_id = args.config_id
    log_id = args.log_id

    # 记录爬取开始时间
    update_crawl_log_start(log_id)

    try:
        items_found, items_new, items_updated = crawl(keywords, max_per_kw)
        # 成功：更新日志和配置
        update_crawl_log(log_id, items_found, items_new, items_updated)
        update_config_last_crawl(config_id)
    except Exception as e:
        # 失败：记录错误信息
        update_crawl_log_error(log_id, str(e))
        raise

if __name__ == "__main__":
    main()