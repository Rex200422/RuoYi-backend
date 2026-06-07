"""
[RSS/HTML] 新闻爬虫模板
基于 RuoYi 舆情系统爬虫开发范式

使用：
  cp template_news_spider.py your_site_spider.py
  然后修改 # 配置区 和 fetch_article_list / fetch_article_detail

Windows 开发：
  1. 确保 crawler_config.py 中 DB 配置正确
  2. pip install pymysql requests beautifulsoup4 lxml
  3. python your_site_spider.py --max 3
"""
import os, sys, re, time, random, argparse, hashlib
import requests
from bs4 import BeautifulSoup
import pymysql

# 统一配置（跨平台路径、数据库、代理）
from crawler_config import DB, IMAGE_DIR, MAIN_KEYWORDS, MAX_ARTICLES, MAX_PAGES, REQUEST_TIMEOUT, REQUEST_DELAY, USER_AGENT
from proxy_config import PROXIES
HEADERS = {"User-Agent": USER_AGENT}
from content_utils import clean_content_html, remove_boilerplate_text
from common_db import (
    save_news_article,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)

# ============================================================
# 配置区 — 只需修改这里
# ============================================================
SITE_NAME = "YourSite"                       # 站点名（写入 news_article.source）
BASE_URL = "https://example.com/news"        # 列表页URL
KEYWORDS = MAIN_KEYWORDS                     # 关键词列表（从 crawler_config 继承）
IMAGE_DIR = IMAGE_DIR                        # 图片目录（从 crawler_config 自动切换）


# ============================================================
# 工具函数
# ============================================================
def clean(text):
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted(set(
        k for k in KEYWORDS
        if re.search(rf"\b{re.escape(k)}\b", t)
    )))


def contains_main_keyword(text):
    t = text.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in KEYWORDS)


def download_image(url, identifier, idx=0):
    if not url:
        return ""
    id_hash = hashlib.md5(identifier.encode()).hexdigest()[:16]
    filename = f"{id_hash}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        # 返回相对于 uploadPath 的路径
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
# 列表页解析 — 【在此实现】
# ============================================================
def fetch_article_list(session, max_pages):
    """
    从列表页收集文章链接。
    返回: [{"title": str, "url": str}, ...]
    """
    articles = []
    visited = set()

    for page_num in range(1, max_pages + 1):
        url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
        print(f"  [LIST] Page {page_num}: {url}")
        try:
            resp = session.get(url, headers=HEADERS, proxies=PROXIES, timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")

            # ===== 根据站点结构调整选择器 =====
            for a_tag in soup.select("article a[href], .post-title a, h2 a"):
                href = a_tag.get("href", "").strip()
                title = clean(a_tag.get_text())
                if not href or len(title) < 10:
                    continue
                if href.startswith("/"):
                    href = BASE_URL.rstrip("/") + href
                if href not in visited:
                    visited.add(href)
                    articles.append({"title": title, "url": href})

        except Exception as e:
            print(f"  [LIST] 失败: {e}")

    return articles


# ============================================================
# 文章详情解析 — 【在此实现】
# ============================================================
def fetch_article_detail(session, url, title):
    try:
        resp = session.get(url, headers=HEADERS, proxies=PROXIES, timeout=30, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        h1 = soup.find("h1")
        if h1:
            title = clean(h1.get_text())

        date = ""
        time_tag = soup.find("time")
        if time_tag:
            date = time_tag.get("datetime") or clean(time_tag.get_text())

        content = ""
        article_tag = soup.find("article") or soup.find("div", class_="entry-content")
        if article_tag:
            content = clean_content_html(str(article_tag))
        if not content or len(content) < 100:
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                content = remove_boilerplate_text(meta["content"])
        if not content or len(content) < 100:
            print(f"    [SKIP] 正文太短")
            return None

        cover_url = ""
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            cover_url = og_img["content"]
        cover_path = download_image(cover_url, url) if cover_url else ""

        return {
            "title": title, "url": url, "date": date,
            "keywords": extract_keywords(title + " " + content),
            "content": content, "cover_image": cover_path, "source": SITE_NAME,
        }
    except Exception as e:
        print(f"    [DETAIL] 失败: {e}")
        return None


# ============================================================
# 主流程
# ============================================================
def crawl(max_articles, max_pages, keyword=None):
    print(f"\n{'='*50}")
    print(f"  {SITE_NAME} Spider  max={max_articles}  pages={max_pages}")
    print(f"{'='*50}")

    session = requests.Session()
    items_found = items_new = items_updated = 0

    article_list = fetch_article_list(session, max_pages)
    print(f"\n  收集到 {len(article_list)} 篇文章\n")

    for i, art in enumerate(article_list):
        if (items_new + items_updated) >= max_articles:
            break
        print(f"  [{i+1}] {art['title'][:60]}")

        detail = fetch_article_detail(session, art["url"], art["title"])
        if not detail:
            continue
        if not contains_main_keyword(detail["title"] + " " + detail["content"]):
            print(f"    [SKIP] 无关键词")
            continue

        items_found += 1
        conn = pymysql.connect(**DB)
        cur = conn.cursor()
        try:
            is_new, is_updated = save_news_article(cur, detail)
            conn.commit()
            if is_new:
                items_new += 1
                print(f"    [NEW] +1")
            elif is_updated:
                items_updated += 1
                print(f"    [UPDATE] +1")
            else:
                print(f"    [NOCHANGE]")
        finally:
            cur.close()
            conn.close()
        time.sleep(random.uniform(*REQUEST_DELAY))

    print(f"\n  Done. found={items_found} new={items_new} updated={items_updated}\n")
    return items_found, items_new, items_updated


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
    update_crawl_log_start(args.log_id)
    try:
        found, new, updated = crawl(
            args.max or MAX_ARTICLES,
            MAX_PAGES,
            args.keyword,
        )
        update_crawl_log(args.log_id, found, new, updated)
        update_config_last_crawl(args.config_id)
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))
        raise


if __name__ == "__main__":
    main()
