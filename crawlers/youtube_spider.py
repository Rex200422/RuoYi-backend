import os, sys, re, time, random, argparse, hashlib, uuid
import requests
import yt_dlp
from playwright.sync_api import sync_playwright

# ============================================================
# 导入统一配置和工具模块
# ============================================================
from crawler_config import (
    DB, IMAGE_DIR, MAX_PER_KEYWORD, REQUEST_DELAY, 
    USER_AGENT, PLAYWRIGHT_PROXY
)
from proxy_config import PROXIES
from common_db import (
    get_db, save_social_post, save_social_comment, save_social_post_image,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)


# ===== 配置区：需要根据目标站点修改 =====
# ============================================================
# 站点配置 — 创建新爬虫时只需修改这里
# ============================================================
SITE_NAME = "YouTube"                       # 站点名
from crawler_config import ALL_KEYWORDS as ALL_KEYWORDS


# ============================================================
# 工具函数
# ============================================================

def clean(text):
    """
    清理文本中的多余空白字符。
    """
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_keywords(text):
    """
    从文本中提取匹配的关键词。
    """
    t = text.lower()
    return ",".join(sorted(set(
        k for k in ALL_KEYWORDS
        if re.search(rf"\b{re.escape(k)}\b", t)
    )))


# ============================================================
# 图片下载函数
# ============================================================

def download_image(url, post_id, idx=0):
    """
    下载图片到本地目录。
    """
    if not url:
        return ""
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
# 搜索帖子 — 【核心提取逻辑实现】
# ============================================================

def search_posts(keyword, max_count):
    """
    根据关键词搜索 YouTube 视频并返回结果。
    
    1. 通过 yt-dlp 的 ytsearch 协议高速获取视频元数据
    2. 通过 Playwright 模拟用户滚动行为抓取懒加载评论
    """
    posts = []
    
    # 1. 配置 yt-dlp 参数进行无界面、不下载视频的 API 式检索
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'proxy': PROXIES['http'] if PROXIES else None,
        'extract_flat': False,  # 提取完整元数据而非仅列表
    }
    
    search_query = f"ytsearch{max_count}:{keyword}"
    print(f"  [yt-dlp] 正在通过后端接口搜索关键词: '{keyword}'...")
    
    video_entries = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(search_query, download=False)
            if 'entries' in search_result:
                video_entries = list(search_result['entries'])
    except Exception as e:
        print(f"  [yt-dlp] 搜索提取失败: {e}")
        return posts

    if not video_entries:
        print("  [yt-dlp] 未检索到相关视频信息")
        return posts

    # 2. 启动 Playwright 集中处理获取到视频的动态评论
    with sync_playwright() as p:
        print("  [Playwright] 启动自动化浏览器实例...")
        browser = p.chromium.launch(headless=True, proxy=PLAYWRIGHT_PROXY)
        
        for entry in video_entries:
            if not entry:
                continue
            
            video_id = entry.get("id")
            video_url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
            print(f"    -> 正在解析视频页: {entry.get('title')[:30]}... ({video_url})")
            
            # 转换发布时间 YYYYMMDD -> YYYY-MM-DD 00:00:00
            raw_date = entry.get("upload_date")
            publish_time = ""
            if raw_date and len(raw_date) == 8:
                publish_time = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]} 00:00:00"
            
            comments = []
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(video_url, timeout=45000)
                
                # 触发 YouTube 评论区初始化的关键步骤：先向下方滚动一段距离
                page.evaluate("window.scrollTo(0, 500);")
                time.sleep(2)
                
                try:
                    # 确保评论区根节点被渲染出来
                    page.wait_for_selector("#comments", timeout=10000)
                except Exception:
                    print("      [提示] 评论区未能快速加载，尝试追加深层滚动...")
                
                # 循环向下滚动若干次，让懒加载的动态评论流式加载出来
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight);")
                    time.sleep(2)
                
                # YouTube 评论线程标准选择器：ytd-comment-thread-renderer
                comment_threads = page.query_selector_all("ytd-comment-thread-renderer")
                
                for idx, thread in enumerate(comment_threads):
                    try:
                        comment_id_el = thread.query_selector("#comment")
                        c_id = comment_id_el.get_attribute("data-id") if comment_id_el else f"{video_id}_c_{idx}"
                        if not c_id:
                            c_id = str(uuid.uuid4())[:16]
                            
                        author_el = thread.query_selector("#author-text")
                        author = clean(author_el.inner_text()) if author_el else "Unknown"
                        
                        content_el = thread.query_selector("#content-text")
                        content = clean(content_el.inner_text()) if content_el else ""
                        if not content:
                            continue  # 无内容评论不作保留
                            
                        like_el = thread.query_selector("#vote-count-middle")
                        like_str = clean(like_el.inner_text()) if like_el else "0"
                        like_count = int(like_str) if like_str.isdigit() else 0
                        
                        time_el = thread.query_selector("yt-formatted-string.published-time-text a")
                        comment_time = clean(time_el.inner_text()) if time_el else ""
                        
                        # 组装评论数据结构
                        comments.append({
                            "comment_id": c_id,
                            "commenter": author,
                            "comment_content": content,
                            "like_count": like_count,
                            "comment_time": comment_time
                        })
                    except Exception:
                        continue
                        
                page.close()
            except Exception as pe:
                print(f"      [Playwright] 评论流捕获异常: {pe}")
            
            # 组装主帖子/视频数据结构
            post_item = {
                "post_id": video_id,
                "title": entry.get("title", ""),
                "author": entry.get("uploader", ""),
                "content": entry.get("description", ""),
                "publish_time": publish_time,
                "like_count": entry.get("like_count", 0) or 0,
                "comment_count": entry.get("comment_count", 0) or 0,
                "original_url": video_url,
                "image_urls": [entry.get("thumbnail")] if entry.get("thumbnail") else [],
                "comments": comments
            }
            posts.append(post_item)
            
        browser.close()
        
    return posts


# ============================================================
# 入库逻辑
# ============================================================

def save_posts(posts, keyword):
    """
    将搜索到的帖子保存到数据库。
    """
    items_found = items_new = items_updated = 0

    for post in posts:
        items_found += 1
        post_id = post["post_id"]

        conn = get_db()
        cur = conn.cursor()
        try:
            # 下载图片
            image_path = ""
            if post.get("image_urls"):
                image_path = download_image(post["image_urls"][0], post_id, 0)
                for idx, img_url in enumerate(post["image_urls"]):
                    local = download_image(img_url, post_id, idx)
                    if local:
                        cur.execute(
                            "INSERT INTO social_post_image (post_id, image_url, local_path, idx) "
                            "VALUES (%s, %s, %s, %s)", (post_id, img_url, local, idx))

            # 保存帖子
            post_data = {
                "uuid": str(uuid.uuid4()), "site_name": SITE_NAME,
                "post_id": post_id, "trigger_keyword": keyword,
                "title": post.get("title", ""),
                "author": post.get("author", ""),
                "publish_time": post.get("publish_time", ""),
                "like_count": post.get("like_count", 0),
                "comment_count": post.get("comment_count", 0),
                "content": post.get("content", ""),
                "original_url": post.get("original_url", ""),
                "image_url": image_path,
            }
            is_new, is_updated = save_social_post(cur, post_data)
            if is_new:
                items_new += 1
                print(f"    [NEW] {post.get('author', '?')}: {post.get('content', '')[:50]}")
            elif is_updated:
                items_updated += 1
                print(f"    [UPDATE] {post_id[:30]}")

            # 保存评论
            for c in post.get("comments", []):
                save_social_comment(cur, {
                    "post_id": post_id,
                    "title": post.get("title", ""),
                    "comment_id": c["comment_id"],
                    "commenter": c.get("commenter", ""),
                    "comment_content": c.get("comment_content", ""),
                    "like_count": c.get("like_count", 0),
                    "comment_time": c.get("comment_time", ""),
                })

            conn.commit()
        finally:
            cur.close()
            conn.close()
        time.sleep(random.uniform(*REQUEST_DELAY))

    return items_found, items_new, items_updated


# ============================================================
# 主流程
# ============================================================

def crawl(keywords, max_per_kw):
    """
    爬虫主流程：按关键词搜索 → 保存帖子。
    """
    print(f"\n{'='*50}")
    print(f"  {SITE_NAME} Spider  keywords={keywords}  max={max_per_kw}")
    print(f"{'='*50}")

    total_found = total_new = total_updated = 0

    for kw in keywords:
        print(f"\n--- 搜索: {kw} ---")
        posts = search_posts(kw, max_per_kw * 3)[:max_per_kw]
        found, new, updated = save_posts(posts, kw)
        total_found += found
        total_new += new
        total_updated += updated
        print(f"  {kw}: found={found} new={new} updated={updated}")

    print(f"\n  Done. found={total_found} new={total_new} updated={total_updated}\n")
    return total_found, total_new, total_updated


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。
    """
    arg_parser = argparse.ArgumentParser(description=f"{SITE_NAME} Spider")
    arg_parser.add_argument("--config-id", type=int, default=None)
    arg_parser.add_argument("--keyword", type=str, default=None)
    arg_parser.add_argument("--max", type=int, default=None)
    arg_parser.add_argument("--log-id", type=int, default=None)
    return arg_parser.parse_args()


def main():
    """
    主函数：解析参数 → 执行爬虫 → 更新日志。
    """
    args = parse_args()
    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()] if args.keyword else ALL_KEYWORDS
    max_per_kw = args.max or MAX_PER_KEYWORD

    update_crawl_log_start(args.log_id)
    try:
        found, new, updated = crawl(keywords, max_per_kw)
        update_crawl_log(args.log_id, found, new, updated)
        update_config_last_crawl(args.config_id)
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))
        raise


if __name__ == "__main__":
    main()