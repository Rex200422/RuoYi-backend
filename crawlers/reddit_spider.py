"""
reddit_spider.py
Reddit Spider (Selenium JSON version)
Refactored to resemble bluesky_spider.py architecture.
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
import shutil
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

from proxy_config import PROXIES
from process_cleanup import cleanup_child_processes, kill_orphaned_processes
from common_db import (
    get_db,
    save_social_post,
    update_crawl_log,
    update_crawl_log_error,
    update_config_last_crawl,
    update_crawl_log_start
)
from crawler_config import IMAGE_DIR



def create_driver():
    """启动 Chrome（通过 Xvfb 虚拟显示运行，避免 Reddit 反爬检测）"""
    import subprocess
    xvfb_proc = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"])
    os.environ["DISPLAY"] = ":99"
    time.sleep(0.5)
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        os.environ.pop(k, None)
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    proxy_server = PROXIES["http"].replace("http://", "")
    options.add_argument(f"--proxy-server={proxy_server}")
    options.add_argument("--proxy-bypass-list=<-loopback>")
    chromedriver_path = shutil.which("chromedriver") or "/usr/bin/chromedriver"
    service = Service(chromedriver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver, xvfb_proc


os.environ["HTTP_PROXY"] = PROXIES["http"]
os.environ["HTTPS_PROXY"] = PROXIES["https"]

ALL_KEYWORDS = ["china", "taiwan"]
DEFAULT_MAX_PER_KW = 2
COMMENT_LIMIT = 20


def clean_text(text):
    return str(text).replace("\\n", " ").replace("\\r", " ").strip() if text else ""


def save_comment(cur, c):
    sql = """
    INSERT INTO social_comment
    (post_id,title,comment_id,commenter,comment_content,like_count,comment_time)
    VALUES(%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
    like_count=VALUES(like_count),
    comment_content=VALUES(comment_content)
    """
    cur.execute(sql, (
        c["post_id"], c["title"], c["comment_id"],
        c["commenter"], c["comment_content"],
        c["like_count"], c["comment_time"]
    ))


def update_engagement(cur, post_id, score, comment_count):
    cur.execute(
        """UPDATE social_post
           SET like_count=%s,comment_count=%s
           WHERE post_id=%s AND site_name='Reddit'""",
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
        r = requests.get(url, proxies=PROXIES, timeout=30)
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
        """INSERT INTO social_post_image
           (post_id,image_url,local_path,idx)
           VALUES(%s,%s,%s,%s)""",
        (post_id, image_url, local_path, 0)
    )
    conn.commit()
    return local_path


def fetch_search_results(driver, keyword, limit):
    url = f"https://www.reddit.com/search.json?q={keyword}&sort=new&limit={limit}"

    driver.get("https://www.reddit.com")
    time.sleep(3)

    driver.get(url)
    time.sleep(4)

    raw = driver.find_element(By.TAG_NAME, "body").text
    data = json.loads(raw)

    return data.get("data", {}).get("children", [])


def fetch_comments(driver, permalink):
    comments = []

    try:
        url = f"https://www.reddit.com{permalink}.json?sort=new&limit={COMMENT_LIMIT}"
        driver.get(url)
        time.sleep(3)

        raw = driver.find_element(By.TAG_NAME, "body").text
        data = json.loads(raw)

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
    driver, xvfb_proc = create_driver()
    conn = get_db()
    cur = conn.cursor()

    items_found = 0
    items_new = 0
    items_updated = 0

    try:
        for kw in keywords:

            posts = fetch_search_results(
                driver,
                kw,
                max_per_kw * 5
            )

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
                publish_time = datetime.fromtimestamp(
                    int(created)
                ).strftime("%Y-%m-%d %H:%M:%S")

                post_url = f"https://www.reddit.com{permalink}"

                img_url = p.get("url_overridden_by_dest", "")
                if not any(img_url.lower().endswith(x)
                           for x in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
                    img_url = ""

                cur.execute(
                    "SELECT 1 FROM social_post WHERE post_id=%s",
                    (post_id,)
                )

                exists = cur.fetchone()

                if exists:
                    update_engagement(
                        cur,
                        post_id,
                        score,
                        num_comments
                    )
                    items_updated += 1

                else:
                    image_local = save_images(
                        cur,
                        conn,
                        post_id,
                        img_url
                    )

                    is_new, is_updated = save_social_post(
                        cur,
                        {
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
                        }
                    )

                    if is_new:
                        items_new += 1
                    if is_updated:
                        items_updated += 1

                comments = fetch_comments(driver, permalink)

                for c in comments:
                    save_comment(
                        cur,
                        {
                            "post_id": post_id,
                            "title": title,
                            "comment_id": str(c.get("id")),
                            "commenter": c.get("author", ""),
                            "comment_content": clean_text(c.get("body", "")),
                            "like_count": c.get("score", 0),
                            "comment_time": publish_time
                        }
                    )

                conn.commit()
                items_found += 1
                count += 1

    finally:
        cleanup_child_processes()
        cur.close()
        conn.close()
        driver.quit()
    if xvfb_proc: xvfb_proc.terminate()

    return items_found, items_new, items_updated


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-id", type=int)
    parser.add_argument("--log-id", type=int)
    parser.add_argument("--keyword")
    parser.add_argument("--max", type=int)
    return parser.parse_args()


def main():
    args = parse_args()

    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()] if args.keyword else ALL_KEYWORDS
    max_per_kw = args.max or DEFAULT_MAX_PER_KW

    # 启动前清理残留进程
    kill_orphaned_processes()
    update_crawl_log_start(args.log_id)

    try:
        found, new, updated = crawl(
            keywords,
            max_per_kw
        )

        update_crawl_log(
            args.log_id,
            found,
            new,
            updated
        )

        update_config_last_crawl(
            args.config_id
        )

    except Exception as e:
        update_crawl_log_error(
            args.log_id,
            str(e)
        )
        raise


if __name__ == "__main__":
    main()
