"""
X (Twitter) 社交媒体爬虫
========================

数据获取方案（基于 xcrawler 的 Node.js 方案移植为 Python）：

1. 登录方式：复用已登录的 Edge Profile（Playwright persistent context）
   - Edge Profile 保存了 auth_token / ct0 等关键 Cookie
   - 登录有效期至 2027 年，无需每次手动登录
   - 注意：不设置系统代理环境变量，通过 Chrome --proxy-server 设置

2. 搜索方式：
   - URL: https://x.com/search?q={keyword}&src=typed_query&f=live
   - 使用搜索关键词过滤数据

3. 数据提取：
   - 解析 <article> 标签提取帖子
   - 提取字段：文本、作者、handle、时间戳、回复/转发/点赞数、图片URL、视频封面URL
   - 帖子链接（/status/ 路径）作为去重依据

4. 翻页方式：
   - 模拟滚动触发瀑布流加载
   - 每轮滚动后检查当前提取数，直到达到目标条数或无更多数据

5. 过滤条件：仅关键词过滤 + 数据库去重

关键词: china, taiwan
每个关键词最多爬取 max_per_kw 条帖子
"""
import os
import sys
import re
import uuid
import hashlib
import argparse
import time
import random
import datetime

# ============================================================
# 代理配置
# ============================================================
from proxy_config import PROXIES

# 注意：不设置系统代理环境变量，通过 Playwright launch 的 proxy 参数设置
# 避免 Selenium/Playwright 驱动内部通信走代理导致死锁

from playwright.sync_api import sync_playwright

from process_cleanup import kill_child_group, kill_orphaned_processes, ensure_clean_before_crawl
from common_db import get_db, save_social_post, save_social_comment, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
from crawler_config import ALL_KEYWORDS


# ============================================================
# 配置常量
# ============================================================

DEFAULT_MAX_PER_KW = 10
# Edge Profile 目录（已登录的 X 账号）
EDGE_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_edge_profile_x")
# 搜索 URL 模板
X_SEARCH_URL = "https://x.com/search?q={keyword}&src=typed_query&f=live"


# ============================================================
# 工具函数
# ============================================================

def clean_text(text):
    """清理文本中的多余空白字符。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_metric(value):
    """将 X 的数字指标文本（如 "1.2K"）转换为整数。"""
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    match = re.match(r"([\d.]+)\s*([KMBkmb]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    suffix = match.group(2).upper()
    multipliers = {"K": 1000, "M": 1000000, "B": 1000000000}
    return int(number * multipliers.get(suffix, 1))


def build_search_url(keyword):
    """构建 X 搜索 URL。"""
    return X_SEARCH_URL.format(keyword=keyword)


# ============================================================
# 帖子提取逻辑
# ============================================================

def extract_posts_from_page(page):
    """
    从当前页面提取帖子数据。
    解析 <article> 标签，提取文本、作者、handle、时间、互动数据、图片。
    """
    posts = page.evaluate("""
        () => {
            const articles = document.querySelectorAll('article');
            const results = [];
            for (const article of articles) {
                try {
                    const text = article.innerText || '';
                    
                    // 提取帖子链接
                    const statusLink = article.querySelector('a[href*="/status/"]');
                    const postUrl = statusLink 
                        ? new URL(statusLink.getAttribute('href'), 'https://x.com').toString()
                        : '';
                    
                    // 提取时间
                    const timeEl = article.querySelector('time');
                    const timestamp = timeEl ? timeEl.getAttribute('datetime') : '';
                    
                    // 提取 handle
                    const handleMatch = text.match(/@[^\\s]+/);
                    const handle = handleMatch ? handleMatch[0] : '';
                    
                    // 提取作者名（第一行）
                    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                    const authorName = lines[0] || '';
                    
                    // 提取互动数据
                    const replyEl = article.querySelector('[data-testid="reply"]');
                    const retweetEl = article.querySelector('[data-testid="retweet"]');
                    const likeEl = article.querySelector('[data-testid="like"]');
                    const viewEl = article.querySelector('a[href$="/analytics"]');
                    
                    const replyCount = replyEl ? replyEl.getAttribute('aria-label') || '' : '';
                    const repostCount = retweetEl ? retweetEl.getAttribute('aria-label') || '' : '';
                    const likeCount = likeEl ? likeEl.getAttribute('aria-label') || '' : '';
                    const viewCount = viewEl ? viewEl.innerText || '' : '';
                    
                    // 提取图片 URL
                    const imageUrls = [...article.querySelectorAll('img')]
                        .map(img => img.currentSrc || img.src || '')
                        .filter(src => /pbs\\.twimg\\.com\\/media\\//.test(src));
                    
                    // 提取视频封面 URL
                    const videoPosterUrls = [...article.querySelectorAll('video')]
                        .map(video => video.poster || '')
                        .filter(Boolean);
                    
                    results.push({
                        postUrl, authorName, handle, text, timestamp,
                        replyCount, repostCount, likeCount, viewCount,
                        imageUrls, videoPosterUrls
                    });
                } catch (e) {
                    // 跳过解析失败的帖子
                }
            }
            return results;
        }
    """)
    return posts


# ============================================================
# 爬虫主流程
# ============================================================

def crawl(keywords, max_per_kw):
    """
    爬虫主流程：使用 Playwright + Edge Profile 访问 X 搜索页。
    每轮滚动后提取帖子，直到达到目标条数或无更多数据。
    """
    print("=== X (Twitter) 爬虫 ===")
    os.makedirs(EDGE_PROFILE_DIR, exist_ok=True)

    conn = get_db()
    cur = conn.cursor()
    items_found = 0
    items_new = 0
    items_updated = 0

    with sync_playwright() as p:
        print("  [Playwright] 启动浏览器（使用已登录的 Edge Profile）...")

        # 使用 proxy 参数设置代理
        proxy_settings = {"server": PROXIES["http"]} if PROXIES.get("http") else None

        # 优先使用 Cookie 注入方式（Edge Profile 在 Linux 上无法直接使用）
        cookie_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "x_cookies.json")
        
        if os.path.exists(cookie_file):
            print("  [INFO] 使用 Cookie 注入方式登录")
            context = p.chromium.launch_persistent_context(
                EDGE_PROFILE_DIR,
                headless=True,
                proxy=proxy_settings,
                viewport={"width": 1365, "height": 900},
                locale="zh-CN",
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                timeout=60000,
            )
            # 注入 Cookie
            import json
            with open(cookie_file, "r") as f:
                cookie_data = json.load(f)
            cookies = cookie_data.get("cookies", [])
            # 过滤有效 Cookie
            valid_cookies = []
            for c in cookies:
                if c.get("value") and c.get("domain"):
                    valid_cookies.append(c)
            if valid_cookies:
                context.add_cookies(valid_cookies)
                print(f"  [INFO] 注入 {len(valid_cookies)} 个 Cookie")
        else:
            print("  [WARN] 未找到 x_cookies.json，尝试直接使用 Edge Profile")
            context = p.chromium.launch_persistent_context(
                EDGE_PROFILE_DIR,
                headless=True,
                proxy=proxy_settings,
                viewport={"width": 1365, "height": 900},
                locale="zh-CN",
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                timeout=60000,
            )

        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(60000)

        try:
            for kw in keywords:
                print(f"\n--- 搜索: {kw} ---")
                search_url = build_search_url(kw)
                cnt = 0

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    # 等待页面初始加载
                    page.wait_for_timeout(random.randint(5000, 8000))

                    # 检测登录墙
                    body_text = page.locator("body").inner_text(timeout=10000) or ""
                    if is_login_wall(body_text):
                        print("  [ERR] 检测到登录墙，登录态可能已失效")
                        break

                    # 滚动加载 + 提取，直到达到目标条数或无更多数据
                    max_scroll_rounds = 10  # 防止无限滚动
                    no_new_rounds = 0

                    for scroll_round in range(max_scroll_rounds):
                        if cnt >= max_per_kw:
                            break

                        # 提取当前页面的帖子
                        raw_posts = extract_posts_from_page(page)
                        prev_cnt = cnt

                        for record in raw_posts:
                            if cnt >= max_per_kw:
                                break
                            if not record.get("text") and not record.get("postUrl"):
                                continue

                            items_found += 1

                            post_id = hashlib.md5(
                                (record.get("postUrl") or record.get("text", "")[:100]).encode()
                            ).hexdigest()

                            # 解析发布时间
                            publish_time = ""
                            ts = record.get("timestamp", "")
                            if ts:
                                try:
                                    publish_time = datetime.datetime.fromisoformat(
                                        ts.replace("Z", "+00:00")
                                    ).strftime("%Y-%m-%d %H:%M:%S")
                                except Exception:
                                    publish_time = ts

                            # 下载第一张图片到本地
                            image_path = ""
                            image_urls = record.get("imageUrls", [])
                            if image_urls:
                                image_path = download_image(image_urls[0], post_id, 0)

                            post_data = {
                                "uuid": str(uuid.uuid4()),
                                "site_name": "X",
                                "trigger_keyword": kw,
                                "source_board": "search",
                                "post_id": post_id,
                                "title": clean_text(record.get("text", ""))[:100],
                                "author": clean_text(record.get("authorName", "")),
                                "publish_time": publish_time,
                                "like_count": normalize_metric(record.get("likeCount", "0")),
                                "comment_count": normalize_metric(record.get("replyCount", "0")),
                                "content": clean_text(record.get("text", "")),
                                "original_url": record.get("postUrl", ""),
                                "image_url": image_path,
                            }

                            is_new, is_updated = save_social_post(cur, post_data)
                            conn.commit()

                            if is_new:
                                items_new += 1
                                print(f"  [NEW] {record.get('authorName', '?')[:20]}: {clean_text(record.get('text', ''))[:60]}")
                            if is_updated:
                                items_updated += 1

                            cnt += 1

                        if cnt == prev_cnt:
                            no_new_rounds += 1
                            if no_new_rounds >= 2:
                                print(f"  [INFO] 连续两轮滚动无新数据，停止")
                                break
                        else:
                            no_new_rounds = 0

                        # 滚动触发加载更多
                        page.mouse.wheel(0, 650)
                        page.wait_for_timeout(random.randint(3000, 6000))

                except Exception as e:
                    print(f"  [ERR] 搜索关键词 【{kw}】 时发生异常: {e}")

                time.sleep(random.uniform(3, 5))
                print(f"  {kw}: {cnt}条")

        finally:
            context.close()
            cur.close()
            conn.close()

    print("\n=== 完成 ===")
    return items_found, items_new, items_updated


# ============================================================
# 辅助函数
# ============================================================

def is_login_wall(body_text):
    """检测是否遇到登录墙。"""
    normalized = re.sub(r"\s+", " ", body_text).lower()
    has_action = bool(re.search(r"log in|sign in|登录|注册", normalized))
    has_marketing = bool(re.search(r"happening now|join today|已有账号|正发生", normalized))
    has_timeline = bool(re.search(r"home timeline|for you following|主页|首页", normalized))
    return has_action and has_marketing and not has_timeline


def download_image(url, post_id, idx=0):
    """下载图片到本地目录。"""
    if not url:
        return ""
    import requests
    from crawler_config import IMAGE_DIR
    post_hash = hashlib.md5(post_id.encode()).hexdigest()[:16]
    filename = f"{post_hash}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        return os.path.relpath(local_path, os.path.dirname(IMAGE_DIR)).replace("\\", "/")
    try:
        resp = requests.get(url, proxies=PROXIES, timeout=30)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        print(f"    [IMG] {filename} ({len(resp.content)} bytes)")
        return os.path.relpath(local_path, os.path.dirname(IMAGE_DIR)).replace("\\", "/")
    except Exception as e:
        print(f"    [IMG] 下载失败: {e}")
        return ""


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="X (Twitter) Spider")
    parser.add_argument("--config-id", type=int, default=None)
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--log-id", type=int, default=None)
    return parser.parse_args()


def main():
    """主函数：解析参数 → 执行爬虫 → 更新日志。"""
    args = parse_args()
    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()] if args.keyword else ALL_KEYWORDS
    max_per_kw = args.max if args.max is not None else DEFAULT_MAX_PER_KW

    # 启动前清理残留进程
    ensure_clean_before_crawl()
    update_crawl_log_start(args.log_id)

    try:
        items_found, items_new, items_updated = crawl(keywords, max_per_kw)
        update_crawl_log(args.log_id, items_found, items_new, items_updated)
        update_config_last_crawl(args.config_id)
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))
        raise
    finally:
        pass

if __name__ == "__main__":
    main()
