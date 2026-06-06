"""
CNN 报刊杂志新闻爬虫 v2 - 保留HTML格式
"""
import os, sys, re, time, random, argparse, requests
from bs4 import BeautifulSoup
import pymysql
from content_utils import clean_content_html
from proxy_config import PROXIES
from common_db import save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start

DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "Referer": "https://edition.cnn.com/"}
DEFAULT_MAX_ARTICLES = 3
CNN_RSS_FEEDS = ["http://rss.cnn.com/rss/edition.rss", "http://rss.cnn.com/rss/edition_world.rss"]

def get_db(): return pymysql.connect(**DB_CONFIG)
def clean(text): return re.sub(r"\s+", " ", text).strip()
# 页脚/版权等无用文本模式
BOILERPLATE_PATTERNS = [
    r"©\s*\d{4}\s*Cable News Network",
    r"All Rights Reserved",
    r"Most stock quote data",
    r"Chicago Mercantile",
    r"Click to expand\s*Image",
    r"^Image:.*",
    r"^Photo:.*",
    r"\(Photo\).*",
    r"Share\s*(This|on Facebook|on Twitter)",
    r"^Share$",
    r"^Print This Post$",
    r"^SHARE$",
    r"^\|.*$",
    r"Most stock quote data",
    r"Chicago Mercantile",
    r"Scan the QR code",
    r"Download the CNN app",
    r"CNN values your feedback",
    r"Cable News Network.*Warner",
    r"A Warner Bros",
    r"CNN Sans",
    r"Sign up for CNN Newsletters",
]

def is_boilerplate(text):
    """检查是否是页脚/版权等无用内容"""
    for pattern in BOILERPLATE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False




def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted([k for k in ["china","taiwan","trade","technology","military","economy","politics","health","climate"] if re.search(rf"\b{k}\b", t)]))

def get_article_content(url):
    """从CNN文章提取全文内容，保留段落格式"""
    invalid = ["/collections/", "/video/", "/interactive/", "live-news"]
    if any(kw in url for kw in invalid):
        return ""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=25, verify=False, allow_redirects=True)
            r.raise_for_status()
            # 如果最终URL变成了分类页面则跳过
            final_url = r.url
            if any(x in final_url for x in ["/specials/", "/videos/", "utm_source=section"]):
                return ""
            text = r.text
            # 方法1: 从嵌入script中提取articleBody（CNN全文）
            marker = 'articleBody'
            pos = text.find(marker)
            while pos != -1:
                s = text.find('"', pos + 12)
                if s == -1: break
                e = s + 1
                while e < len(text):
                    if text[e] == chr(92) and e + 1 < len(text):
                        e += 2
                        continue
                    if text[e] == chr(34):
                        break
                    e += 1
                if e > s + 100:
                    body = text[s+1:e]
                    body = body.replace('\\n', chr(10)).replace('\\/', '/').replace('\\u003C', '<').replace('\\u003E', '>')
                    body = re.sub(r'<[^>]+>', '', body)
                    if len(body) > 100:
                        paras = [p.strip() for p in body.split(chr(10)) if p.strip()]
                        if len(paras) <= 1:
                            paras = [p.strip() for p in re.split(r'\s{3,}', body) if p.strip()]
                        parts = []
                        for p in paras:
                            if len(p) > 10 and not is_boilerplate(p):
                                parts.append('<p>' + p + '</p>')
                        if parts:
                            html = chr(10).join(parts)
                            return clean_content_html(html)[:8000]
                pos = text.find(marker, pos + 1)
            # 方法2: meta description (fallback)
            soup = BeautifulSoup(text, "html.parser")
            for meta in soup.find_all("meta"):
                name = meta.get("name", "") or meta.get("property", "")
                val = meta.get("content", "")
                if name in ("description", "og:description") and val:
                    if not is_boilerplate(val) and len(val) > 30:
                        return '<p>' + val + '</p>'
            return ""
        except Exception as e:
            if attempt == 2:
                print(f"  [WARN] 正文抓取失败: {url[:50]}... | {e}")
                return ""
            time.sleep(2)


def crawl_cnn(max_articles, keyword=None):
    """Core crawl logic. Returns (items_found, items_new, items_updated) counts."""
    print(f"=== CNN 新闻爬虫 v2 (HTML格式) ===")
    if keyword:
        print(f"关键词: {keyword}")
    print(f"最大文章数: {max_articles}")
    conn = get_db(); cursor = conn.cursor(); count = 0; items_found = 0; items_new = 0; items_updated = 0
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
                    is_new, is_updated = save_news_article(cursor, article)
                    if is_new: items_new += 1
                    if is_updated: items_updated += 1
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
    return items_found, items_new, items_updated

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
    update_crawl_log_start(log_id)

    try:
        items_found, items_new, items_updated = crawl_cnn(max_articles, keyword=args.keyword)
        # On success: update log and config
        update_crawl_log(log_id, items_found, items_new, items_updated)
        update_config_last_crawl(config_id)
    except Exception as e:
        # On error: update log with error
        update_crawl_log_error(log_id, str(e))
        raise

if __name__ == "__main__":
    main()
