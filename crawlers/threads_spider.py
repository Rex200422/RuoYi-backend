"""
Threads 社交媒体爬虫
=====================

功能概述：
    使用 Selenium (undetected-chromedriver) 抓取 Threads 平台的帖子。
    按关键词搜索帖子，向下滚动加载数据，保存帖子到数据库。

技术方案：
    1. 使用 uc.Chrome 模拟浏览器访问 Threads 搜索页
    2. 强制等待网页 body 出现，模拟滚动以触发瀑布流
    3. 解析 div[@role="region"] 或 article 区块，提取包含属性 dir='auto' 的 span 文本
    4. 提取原帖链接并使用 MD5 算法生成唯一 post_id
    5. 交由 save_social_post 执行去重和数据库写入

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
# 代理配置初始化
# ============================================================
from proxy_config import PROXIES
# 注意：不设置系统代理环境变量，通过Chrome --proxy-server设置
# 避免Selenium驱动内部通信走代理导致死锁

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from process_cleanup import kill_child_group, kill_orphaned_processes, ensure_clean_before_crawl, start_xvfb, ensure_clean_before_crawl
from common_db import get_db, save_social_post, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
from crawler_config import DB


# ============================================================
# 配置常量
# ============================================================

from crawler_config import ALL_KEYWORDS as ALL_KEYWORDS
DEFAULT_MAX_PER_KW = 5
# 定义 Threads 专属的浏览器缓存路径，隔离登录凭证
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_chrome_profile_threads")


# ============================================================
# 文本处理工具
# ============================================================

def clean_text(text):
    """
    清理文本中的多余换行符。
    """
    if not text: return ""
    return str(text).replace("\n", " ").replace("\r", " ").strip()


# ============================================================
# 爬虫主流程
# ============================================================

def crawl(keywords, max_per_kw):
    """
    爬虫主流程：使用 Selenium 搜索并抓取 Threads 帖子。
    """
    import subprocess
    # 启动 Xvfb 虚拟显示
    xvfb_proc, display = start_xvfb()
    os.environ["DISPLAY"] = f":{display}"
    time.sleep(0.5)
    print("=== Threads 爬虫 ===")
    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
    
    # 浏览器配置
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # 提取纯 "IP:端口" 给 Chrome 配置代理
    proxy_server = PROXIES["http"].replace("http://", "")
    chrome_options.add_argument(f'--proxy-server={proxy_server}')
    chrome_options.add_argument("--proxy-bypass-list=<-loopback>")
    chrome_options.add_argument("--lang=zh-CN,zh;q=0.9")
    
    # 使用系统安装的 ChromeDriver（版本 149，与 Chrome 版本匹配）
    driver_path = "/usr/bin/chromedriver"

    driver = None
    conn = get_db()
    cur = conn.cursor()
    
    items_found = 0
    items_new = 0
    items_updated = 0

    try:
        print("正在拉起浏览器 (使用本地已存的身份凭证)...")
        driver = uc.Chrome(options=chrome_options, user_data_dir=CHROME_PROFILE_DIR, driver_executable_path=driver_path)

        # 清理可能残留的代理环境变量
        for env_key in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
            os.environ.pop(env_key, None)

        for kw in keywords:
            print(f"\n--- 搜索: {kw} ---")
            url = f"https://www.threads.net/search?q={kw}"
            
            try:
                driver.get(url)
                
                # 防御白屏：强制等待网页的 body 标签出现
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                except Exception:
                    print(f"    [WARN] 页面似乎未能正常渲染，可能遇到了网络拦截。")
                
                time.sleep(random.uniform(5.0, 8.0))
                
                # 模拟向下滚动（高容错版）
                scroll_times = 3
                for i in range(scroll_times):
                    print(f"    [INFO] 正在向下滚动页面以加载更多动态 ({i+1}/{scroll_times})...")
                    try:
                        safe_scroll_js = """
                            var h = document.documentElement.scrollHeight || (document.body ? document.body.scrollHeight : 0);
                            window.scrollTo(0, h);
                        """
                        driver.execute_script(safe_scroll_js)
                    except Exception as e:
                        print(f"    [WARN] 滚动时遇到细微异常，已忽略继续: {e}")
                    time.sleep(random.uniform(4.0, 7.0)) 
                
                # 穿透性提取方案
                blocks = driver.find_elements(By.XPATH, '//div[@role="region"] | //article')
                print(f"    [INFO] 发现 {len(blocks)} 个潜在帖子区块，开始提取文本...")
                
                cnt = 0
                for block in blocks:
                    if cnt >= max_per_kw: 
                        break
                    
                    try:
                        items = []
                        spans = block.find_elements(By.XPATH, ".//span[@dir='auto']")
                        
                        for s in spans:
                            txt = s.text.strip()
                            if len(txt) > 2: 
                                items.append(txt)
                                
                        if not items:
                            continue
                            
                        # 字典去重并合并
                        unique_items = list(dict.fromkeys(items))
                        full_text = '\n'.join(unique_items)
                        items_found += 1
                        
                        # 提取原帖链接
                        post_url = f"https://www.threads.net/search?q={kw}"
                        try:
                            a_tags = block.find_elements(By.TAG_NAME, "a")
                            for a in a_tags:
                                href = a.get_attribute("href")
                                if href and "/post/" in href: 
                                    post_url = href
                                    break
                        except Exception:
                            pass
                        
                        # Threads 采用原帖链接的 MD5 作为唯一 post_id
                        post_id = hashlib.md5(post_url.encode()).hexdigest()
                        
                        post_data = {
                            "uuid": str(uuid.uuid4()),
                            "site_name": "Threads",
                            "trigger_keyword": kw,
                            "source_board": "search",
                            "post_id": post_id,
                            "title": clean_text(full_text)[:100],
                            "author": "Threads User",
                            "publish_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            "like_count": 0,
                            "comment_count": 0,
                            "content": full_text,
                            "original_url": post_url,
                            "image_url": ""
                        }
                        
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
        if xvfb_proc:
            xvfb_proc.terminate()
        
    print("=== 完成 ===")
    return items_found, items_new, items_updated


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。
    """
    parser = argparse.ArgumentParser(description="Threads Spider")
    parser.add_argument("--config-id", type=int, default=None, help="crawl_config ID")
    parser.add_argument("--keyword", type=str, default=None, help="Keyword to crawl (overrides default list)")
    parser.add_argument("--max", type=int, default=None, help="Max results per keyword (overrides default)")
    parser.add_argument("--log-id", type=int, default=None, help="crawl_log ID to update")
    parser.add_argument("max_legacy", nargs="?", type=int, default=None, help="(legacy) max per keyword")
    return parser.parse_args()

def main():
    args = parse_args()

    # 启动前清理残留进程
    ensure_clean_before_crawl()

    if args.keyword:
        keywords = [k.strip() for k in args.keyword.split(",") if k.strip()]
    else:
        keywords = ALL_KEYWORDS

    max_per_kw = args.max if args.max is not None else (args.max_legacy if args.max_legacy is not None else DEFAULT_MAX_PER_KW)

    config_id = args.config_id
    log_id = args.log_id

    update_crawl_log_start(log_id)

    try:
        items_found, items_new, items_updated = crawl(keywords, max_per_kw)
        update_crawl_log(log_id, items_found, items_new, items_updated)
        update_config_last_crawl(config_id)
    except Exception as e:
        update_crawl_log_error(log_id, str(e))
        raise

if __name__ == "__main__":
    main()