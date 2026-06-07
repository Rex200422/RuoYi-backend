"""
社媒爬虫模板（帖子+评论）
基于 RuoYi 舆情系统爬虫开发范式

使用：
  cp template_social_spider.py your_site_spider.py
  然后修改 # 配置区 和 search_posts()

Windows 开发：
  1. 确保 crawler_config.py 中 DB 配置正确
  2. pip install pymysql requests
  3. python your_site_spider.py --max 3
"""
import os, sys, re, time, random, argparse, hashlib, uuid
import requests
import pymysql

# 统一配置
from crawler_config import DB, IMAGE_DIR, MAIN_KEYWORDS, MAX_PER_KEYWORD, REQUEST_DELAY
from proxy_config import PROXIES
from common_db import (
    save_social_post, save_social_comment, save_social_post_image,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)

# ============================================================
# 配置区
# ============================================================
SITE_NAME = "YourSite"
ALL_KEYWORDS = MAIN_KEYWORDS


def clean(text):
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted(set(
        k for k in ALL_KEYWORDS
        if re.search(rf"\b{re.escape(k)}\b", t)
    )))


def download_image(url, post_id, idx=0):
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
# 搜索帖子 — 【在此实现】
# ============================================================
def search_posts(keyword, max_count):
    """
    返回: [
        {
            "post_id": str,         # 帖子唯一ID（必填）
            "title": str,           # 标题/摘要（前200字）
            "author": str,          # 作者
            "content": str,         # 完整内容
            "publish_time": str,    # "YYYY-MM-DD HH:MM:SS"
            "like_count": int,
            "comment_count": int,
            "original_url": str,
            "image_urls": list,     # 图片URL列表
            "comments": [           # 评论列表
                {"comment_id": str, "commenter": str, "comment_content": str,
                 "like_count": int, "comment_time": str}
            ]
        }
    ]
    """
    posts = []
    # ===== 在此实现搜索逻辑 =====
    return posts


# ============================================================
# 入库逻辑
# ============================================================
def save_posts(posts, keyword):
    items_found = items_new = items_updated = 0

    for post in posts:
        items_found += 1
        post_id = post["post_id"]

        conn = pymysql.connect(**DB)
        cur = conn.cursor()
        try:
            # 图片
            image_path = ""
            if post.get("image_urls"):
                image_path = download_image(post["image_urls"][0], post_id, 0)
                for idx, img_url in enumerate(post["image_urls"]):
                    local = download_image(img_url, post_id, idx)
                    if local:
                        cur.execute(
                            "INSERT INTO social_post_image (post_id, image_url, local_path, idx) "
                            "VALUES (%s, %s, %s, %s)", (post_id, img_url, local, idx))

            # 帖子
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

            # 评论
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
# 入口
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(description=f"{SITE_NAME} Spider")
    parser.add_argument("--config-id", type=int, default=None)
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--log-id", type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    keywords = [args.keyword] if args.keyword else ALL_KEYWORDS
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
