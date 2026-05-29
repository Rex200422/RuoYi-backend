"""
CNN 报刊杂志新闻爬虫 v2 - 保留HTML格式
"""
import os, sys, re, time, random, argparse, requests
from bs4 import BeautifulSoup
import pymysql

PROXY = "http://192.168.0.14:7890/"
PROXIES = {"http": PROXY, "https": PROXY}
DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "Referer": "https://edition.cnn.com/"}
DEFAULT_MAX_ARTICLES = 3
CNN_RSS_FEEDS = ["http://rss.cnn.com/rss/edition.rss", "http://rss.cnn.com/rss/edition_world.rss"]

def get_db(): return pymysql.connect(**DB_CONFIG)
def clean(text): return re.sub(r"\s+", " ", text).strip()

def update_log_start(log_id):
    """Update crawl_log start_time when crawl begins."""
    if not log_id:
        return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE crawl_log SET start_time=NOW() WHERE id=%s", (log_id,))
        conn.commit()
    finally:
        cur.close(); conn.close()

def update_log_success(log_id, items_found, items_saved):
    """Update crawl_log on success."""
    if not log_id:
        return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE crawl_log SET status='success', end_time=NOW(), items_found=%s, items_saved=%s WHERE id=%s",
                    (items_found, items_saved, log_id))
        conn.commit()
    finally:
        cur.close(); conn.close()

def update_log_error(log_id, error_msg):
    """Update crawl_log on error."""
    if not log_id:
        return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE crawl_log SET status='failed', end_time=NOW(), error_msg=%s WHERE id=%s",
                    (str(error_msg)[:2000], log_id))
        conn.commit()
    finally:
        cur.close(); conn.close()

def update_config_last_crawl(config_id):
    """Update crawl_config last_crawl_time."""
    if not config_id:
        return
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE crawl_config SET last_crawl_time=NOW() WHERE id=%s", (config_id,))
        conn.commit()
    finally:
        cur.close(); conn.close()

def save_article(cursor, article):
    sql = """INSERT INTO news_article (title,url,publish_date,keywords,content,source)
    VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE content=VALUES(content), keywords=VALUES(keywords)"""
    cursor.execute(sql, (article["title"], article["url"], article["date"], article["keywords"], article["content"], article["source"]))

def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted([k for k in ["china","taiwan","trade","technology","military","economy","politics","health","climate"] if re.search(rf"\b{k}\b", t)]))

def get_article_content(url):
    """获取文章正文 - 保留HTML格式"""
    invalid = ["/collections/", "/video/", "/interactive/", "live-news"]
    if any(kw in url for kw in invalid):
        return ""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=25, verify=False, allow_redirects=True)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # 优先找 CNN 正文区域
            content_parts = []
            p_tags = soup.find_all("p", class_="paragraph")
            if not p_tags:
                p_tags = soup.find_all("div", class_="zn-body__paragraph")
            if not p_tags:
                # 通用兜底：所有 p 标签
                for p in soup.find_all("p"):
                    text = clean(p.get_text())
                    if len(text) > 20:
                        content_parts.append(f"<p>{text}</p>")
            else:
                for p in p_tags:
                    text = clean(p.get_text())
                    if text:
                        content_parts.append(f"<p>{text}</p>")

            # 也找 h2/h3 标题
            for h in soup.find_all(["h2", "h3"]):
                text = clean(h.get_text())
                if text and len(text) > 5:
                    content_parts.append(f"<{h.name}>{text}</{h.name}>")

            content = "\n".join(content_parts)
            return content[:8000] if len(content) >= 50 else ""
        except Exception as e:
            if attempt == 2:
                print(f"  [WARN] 正文抓取失败: {url[:50]}... | {e}")
                return ""
            time.sleep(2)

def crawl_cnn(max_articles, keyword=None):
    """Core crawl logic. Returns (items_found, items_saved) counts."""
    print(f"=== CNN 新闻爬虫 v2 (HTML格式) ===")
    if keyword:
        print(f"关键词: {keyword}")
    print(f"最大文章数: {max_articles}")
    conn = get_db(); cursor = conn.cursor(); count = 0; items_found = 0
    try:
        for feed_url in CNN_RSS_FEEDS:
            if count >= max_articles: break
            print(f"\n访问 RSS: {feed_url}")
            try:
                resp = requests.get(feed_url, headers=HEADERS, proxies=PROXIES, timeout=15, verify=False)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                print(f"  获取 {len(items)} 条新闻")
                for item in items:
                    if count >= max_articles: break
                    title_tag = item.find("title")
                    link_tag = item.find("link")
                    if not title_tag or not link_tag: continue
                    title = title_tag.text.strip()
                    href = link_tag.text.strip()
                    if len(title) < 5: continue
                    # If keyword provided, filter articles containing it
                    if keyword and keyword.lower() not in title.lower():
                        continue
                    items_found += 1
                    content = get_article_content(href)
                    if not content:
                        print(f"  [SKIP] {title[:40]}... (无正文)")
                        continue
                    keywords = extract_keywords(title + " " + clean(content))
                    pub_date = ""
                    date_tag = item.find("pubDate")
                    if date_tag: pub_date = date_tag.text.strip()
                    article = {"title": title, "url": href, "date": pub_date, "keywords": keywords, "content": content, "source": "CNN"}
                    save_article(cursor, article)
                    conn.commit()
                    count += 1
                    print(f"  [OK] {count}/{max_articles}: {title[:50]}...")
                    time.sleep(random.uniform(1, 2.5))
            except Exception as e:
                print(f"  [ERROR] RSS抓取失败: {e}")
                continue
        print(f"\n=== 爬取完成，共 {count} 条新闻 ===")
    finally:
        cursor.close(); conn.close()
    return items_found, count

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="CNN Spider")
    parser.add_argument("--config-id", type=int, default=None, help="crawl_config ID")
    parser.add_argument("--keyword", type=str, default=None, help="Keyword to filter articles (logged even if CNN uses RSS)")
    parser.add_argument("--max", type=int, default=None, help="Max articles to crawl (overrides default)")
    parser.add_argument("--log-id", type=int, default=None, help="crawl_log ID to update")
    # Backward compat: allow positional arg as max_articles
    parser.add_argument("max_legacy", nargs="?", type=int, default=None, help="(legacy) max articles")
    return parser.parse_args()

def main():
    args = parse_args()

    # Determine max articles
    max_articles = args.max if args.max is not None else (args.max_legacy if args.max_legacy is not None else DEFAULT_MAX_ARTICLES)

    config_id = args.config_id
    log_id = args.log_id

    # On start: update log start_time
    update_log_start(log_id)

    try:
        items_found, items_saved = crawl_cnn(max_articles, keyword=args.keyword)
        # On success: update log and config
        update_log_success(log_id, items_found, items_saved)
        update_config_last_crawl(config_id)
    except Exception as e:
        # On error: update log with error
        update_log_error(log_id, str(e))
        raise

if __name__ == "__main__":
    main()
