"""
reddit_spider.py
Reddit Spider - 使用 requests 通过代理链访问 Reddit JSON API
"""

import os
import uuid
import json
import time
import random
import hashlib
import argparse
from datetime import datetime

import requests
from proxy_config import PROXIES
from common_db import (
    get_db,
    save_social_post,
    save_social_comment,
    update_crawl_log,
    update_crawl_log_error,
    update_config_last_crawl,
    update_crawl_log_start
)
from retry_utils import with_retry
from crawler_config import IMAGE_DIR, ALL_KEYWORDS

DEFAULT_MAX_PER_KW = 10
COMMENT_LIMIT = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def clean_text(text):
    return str(text).replace("\\n", " ").replace("\\r", " ").strip() if text else ""


def save_comment(cur, c):
    sql = """
    INSERT INTO social_comment
    (post_id,title,comment_id,commenter,comment_content,like_count,comment_time)
    VALUES(%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
    like_count=VALUES(like_count), comment_content=VALUES(comment_content)
    """
    cur.execute(sql, (
        c["post_id"], c["title"], c["comment_id"],
        c["commenter"], c["comment_content"], c["like_count"], c["comment_time"]
    ))


def update_engagement(cur, post_id, score, comment_count):
    cur.execute(
        "UPDATE social_post SET like_count=%s, comment_count=%s WHERE post_id=%s AND site_name='Reddit'",
        (score, comment_count, post_id)
    )


def download_image(url, post_id, idx):
    if not url:
        return None
    os.makedirs(IMAGE_DIR, exist_ok=True)
    filename = f"{hashlib.md5(post_id.encode()).hexdigest()[:16]}_{idx}.jpg"
    path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(path):
        return filename
    try:
        r = with_retry(lambda: requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=30), description=f"下载图片{url[:50]}")
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        return filename
    except Exception:
        return None


def save_images(cur, conn, post_id, image_url):
    if not image_url:
        return ""
    local_file = download_image(image_url, post_id, 0)
    if not local_file:
        return ""
    local_path = f"sentiment/images/{local_file}"
    cur.execute(
        "INSERT INTO social_post_image (post_id, image_url, local_path, idx) VALUES(%s, %s, %s, %s)",
        (post_id, image_url, local_path, 0)
    )
    conn.commit()
    return local_path


def fetch_search_results(keyword, limit):
    """通过 requests + proxy 访问 Reddit JSON API"""
    url = f"https://www.reddit.com/search.json?q={keyword}&sort=new&limit={limit}"
    resp = with_retry(
        lambda: requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=15),
        description=f"Reddit搜索{keyword}"
    )
    data = resp.json()
    return data.get("data", {}).get("children", [])


def fetch_comments(permalink):
    """获取评论"""
    comments = []
    try:
        url = f"https://www.reddit.com{permalink}.json?sort=new&limit={COMMENT_LIMIT}"
        resp = with_retry(
            lambda: requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=15),
            description=f"Reddit评论{permalink[:30]}"
        )
        data = resp.json()
        if len(data) < 2:
            return comments
        for item in data[1]["data"]["children"]:
            if item.get("kind") != "t1":
                continue
            comments.append(item["data"])
    except Exception:
        pass
    return comments


def crawl(keywords, max_per_kw):
    print("=== Reddit 爬虫 ===")
    conn = get_db()
    cur = conn.cursor()
    items_found = 0
    items_new = 0
    items_updated = 0

    try:
        for kw in keywords:
            print(f"\n--- 搜索: {kw} ---")
            try:
                posts = fetch_search_results(kw, max_per_kw * 3)
            except Exception as e:
                print(f"  [ERR] 搜索失败: {e}")
                continue

            count = 0
            for item in posts:
                if count >= max_per_kw:
                    break
                p = item["data"]
                post_id = str(p.get("id"))
                title = clean_text(p.get("title"))
                author = p.get("author", "")
                subreddit = p.get("subreddit", "")
                score = p.get("score", 0)
                num_comments = p.get("num_comments", 0)
                content = clean_text(p.get("selftext", ""))
                permalink = p.get("permalink", "")
                created = p.get("created_utc")
                publish_time = datetime.fromtimestamp(int(created)).strftime("%Y-%m-%d %H:%M:%S") if created else ""
                post_url = f"https://www.reddit.com{permalink}"

                img_url = p.get("url_overridden_by_dest", "")
                if img_url and not any(img_url.lower().endswith(x) for x in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
                    img_url = ""

                cur.execute("SELECT 1 FROM social_post WHERE post_id=%s", (post_id,))
                exists = cur.fetchone()

                if exists:
                    update_engagement(cur, post_id, score, num_comments)
                    items_updated += 1
                else:
                    image_local = save_images(cur, conn, post_id, img_url)
                    is_new, is_updated = save_social_post(cur, {
                        "uuid": str(uuid.uuid4()),
                        "site_name": "Reddit",
                        "trigger_keyword": kw,
                        "source_board": f"r/{subreddit}",
                        "post_id": post_id,
                        "title": title,
                        "author": author,
                        "publish_time": publish_time,
                        "like_count": score,
                        "comment_count": num_comments,
                        "content": content,
                        "original_url": post_url,
                        "image_url": image_local
                    })
                    if is_new:
                        items_new += 1
                        print(f"  [POST] {author}: {title[:60]}")
                    if is_updated:
                        items_updated += 1

                # 评论
                try:
                    comments = fetch_comments(permalink)
                    for c in comments:
                        save_comment(cur, {
                            "post_id": post_id,
                            "title": title,
                            "comment_id": str(c.get("id")),
                            "commenter": c.get("author", ""),
                            "comment_content": clean_text(c.get("body", "")),
                            "like_count": c.get("score", 0),
                            "comment_time": publish_time
                        })
                except Exception:
                    pass

                conn.commit()
                items_found += 1
                count += 1
                time.sleep(random.uniform(1, 3))

            print(f"  {kw}: {count}条")
            time.sleep(random.uniform(2, 4))

    finally:
        cur.close()
        conn.close()

    print("=== 完成 ===")
    return items_found, items_new, items_updated


def parse_args():
    parser = argparse.ArgumentParser(description="Reddit Spider")
    parser.add_argument("--config-id", type=int, default=None)
    parser.add_argument("--log-id", type=int, default=None)
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--max", type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()] if args.keyword else ALL_KEYWORDS
    max_per_kw = args.max if args.max is not None else DEFAULT_MAX_PER_KW

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
