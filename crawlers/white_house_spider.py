"""
White House News Spider - Playwright
https://www.whitehouse.gov/news/
"""
import os, sys, re, time, random, argparse
from playwright.sync_api import sync_playwright
import pymysql
from content_utils import extract_content_playwright, remove_boilerplate_text
import hashlib, requests as req_lib

IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"
PROXY = "http://192.168.0.14:7890/"
DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
SITE_NAME = "White House"
BASE_URL = "https://www.whitehouse.gov"
DEFAULT_MAX_PAGES = 2
DEFAULT_MAX_ARTICLES = 2

MAIN_KEYWORDS = ["china", "taiwan"]
SUB_KEYWORDS = ["trade", "technology", "military", "sanctions", "indo-pacific", "south china sea",
                "semiconductor", "cyber", "beijing", "human rights", "xinjiang", "hong kong",
                "uyghur", "tibet", "ccp"]

def get_db(): return pymysql.connect(**DB_CONFIG)
def clean(text): return re.sub(r"\s+", " ", text).strip() if text else ""

def update_log_start(log_id):
    if not log_id: return
    conn = get_db(); cur = conn.cursor()
    try: cur.execute("UPDATE crawl_log SET start_time=NOW() WHERE id=%s", (log_id,)); conn.commit()
    finally: cur.close(); conn.close()

def update_log_success(log_id, items_found, items_saved):
    if not log_id: return
    conn = get_db(); cur = conn.cursor()
    try: cur.execute("UPDATE crawl_log SET status='success', end_time=NOW(), items_found=%s, items_saved=%s WHERE id=%s", (items_found, items_saved, log_id)); conn.commit()
    finally: cur.close(); conn.close()

def update_log_error(log_id, error_msg):
    if not log_id: return
    conn = get_db(); cur = conn.cursor()
    try: cur.execute("UPDATE crawl_log SET status='failed', end_time=NOW(), error_msg=%s WHERE id=%s", (str(error_msg)[:2000], log_id)); conn.commit()
    finally: cur.close(); conn.close()

def update_config_last_crawl(config_id):
    if not config_id: return
    conn = get_db(); cur = conn.cursor()
    try: cur.execute("UPDATE crawl_config SET last_crawl_time=NOW() WHERE id=%s", (config_id,)); conn.commit()
    finally: cur.close(); conn.close()

def save_article(cursor, article):
    sql = """INSERT INTO news_article (title, url, publish_date, keywords, cover_image, content, source)
    VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE title=VALUES(title), publish_date=VALUES(publish_date),
    content=VALUES(content), keywords=VALUES(keywords), cover_image=VALUES(cover_image)"""
    cursor.execute(sql, (article["title"], article["url"], article["date"], article["keywords"],
                         article.get("cover_image", ""), article["content"], article["source"]))

def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted(set(k for k in MAIN_KEYWORDS + SUB_KEYWORDS if re.search(rf"\b{re.escape(k)}\b", t))))

def contains_keywords(text):
    t = text.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in MAIN_KEYWORDS)

def extract_cover_image(page):
    meta = page.locator("meta[property='og:image']")
    if meta.count() > 0: return meta.first.get_attribute("content") or ""
    meta2 = page.locator("meta[name='twitter:image']")
    if meta2.count() > 0: return meta2.first.get_attribute("content") or ""
    return ""

def extract_date(page):
    meta = page.locator("meta[property='article:published_time']")
    if meta.count() > 0: return meta.first.get_attribute("content") or ""
    t = page.locator("time")
    if t.count() > 0:
        dt = t.first.get_attribute("datetime")
        if dt: return dt
        return clean(t.first.inner_text())
    # Fallback: extract from body text
    body_text = page.locator("body").inner_text()
    match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}", body_text)
    if match: return match.group(0)
    return ""

def crawl(max_pages, max_articles):
    items_found = 0
    items_saved = 0
    page_failures = 0
    visited_urls = set()
    conn = get_db()
    cur = conn.cursor()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, proxy={"server": PROXY},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                locale="en-US", viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

            news_list = []
            # === List pages ===
            for page_num in range(1, max_pages + 1):
                url = f"{BASE_URL}/news/" if page_num == 1 else f"{BASE_URL}/news/page/{page_num}/"
                print(f"\n访问：{url}")
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    time.sleep(2 + random.random())
                except Exception as e:
                    print(f"页面失败：{e}"); page_failures += 1; continue

                items = page.locator("li.wp-block-post")
                print(f"文章块：{items.count()}")
                for i in range(items.count()):
                    try:
                        item = items.nth(i)
                        title_el = item.locator("h2.wp-block-post-title a")
                        if title_el.count() == 0: continue
                        title = clean(title_el.first.inner_text())
                        href = title_el.first.get_attribute("href")
                        if not href or len(title) < 5: continue
                        if href in visited_urls: continue
                        visited_urls.add(href)
                        date = ""
                        date_el = item.locator("time")
                        if date_el.count() > 0: date = clean(date_el.last.inner_text())
                        news_list.append({"title": title, "url": href, "date": date})
                        print(f"收录：{title}")
                    except Exception as e:
                        print(f"列表错误：{e}")

            if page_failures > 0 and len(news_list) == 0:
                raise Exception(f"所有列表页访问失败({page_failures}次)，代理可能不可用")
            print(f"\n总文章数：{len(news_list)}")

            # === Detail pages ===
            for news in news_list:
                if items_saved >= max_articles: break
                try:
                    print(f"\n采集：{news['title']}")
                    page.goto(news["url"], wait_until="networkidle", timeout=60000)
                    time.sleep(1 + random.random())

                    # Scroll to bottom for lazy load
                    scroll_to_bottom(page)

                    h1 = page.locator("h1")
                    if h1.count() > 0: news["title"] = clean(h1.first.inner_text())

                    date = news.get("date", "") or extract_date(page)
                    cover_url = extract_cover_image(page)
                    cover_image = download_image(cover_url, news["url"]) if cover_url else ""

                    # Content: try article first, fallback to main
                    content = extract_content_playwright(page, "article")
                    if not content:
                        # White House uses article p/li/h2/h3 structure
                        elements = page.locator("article p, article li, article h2, article h3")
                        if elements.count() == 0:
                            elements = page.locator("main p, main li, main h2, main h3")
                        texts = []
                        for i in range(elements.count()):
                            try:
                                txt = clean(elements.nth(i).inner_text())
                                if len(txt) > 2: texts.append(f"<p>{txt}</p>")
                            except: pass
                        content = "\n".join(texts)
                    if not content:
                        content = remove_boilerplate_text(page.locator("main").first.inner_text()) if page.locator("main").count() > 0 else ""

                    items_found += 1
                    if not contains_keywords(content):
                        print(f"跳过(无关键词) content_len={len(content)}"); continue

                    keywords = extract_keywords(content)
                    article_data = {
                        "title": news["title"], "url": news["url"], "date": clean(date),
                        "content": content[:5000], "keywords": keywords,
                        "cover_image": "sentiment/images/" + cover_image if cover_image else "",
                        "source": SITE_NAME}
                    save_article(cur, article_data); conn.commit()
                    items_saved += 1; print("已保存")
                    time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"详情错误：{e}")
            browser.close()
    finally:
        cur.close(); conn.close()
    print(f"\n=== Done. Total saved: {items_saved} articles ===")
    return items_found, items_saved

# scroll_to_bottom defined at module level for use in crawl()
def scroll_to_bottom(page):
    try:
        total_height = page.evaluate("document.body.scrollHeight")
        current = 0
        while current < total_height:
            current += 800
            page.evaluate(f"window.scrollTo(0, {current})")
            page.wait_for_timeout(400)
        page.wait_for_timeout(2000)
    except: pass

def parse_args():
    parser = argparse.ArgumentParser(description="White House Spider")
    parser.add_argument("--config-id", type=int, default=None)
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--log-id", type=int, default=None)
    return parser.parse_args()

def main():
    args = parse_args()
    max_articles = args.max if args.max is not None else DEFAULT_MAX_ARTICLES
    update_log_start(args.log_id)
    try:
        items_found, items_saved = crawl(DEFAULT_MAX_PAGES, max_articles)
        update_log_success(args.log_id, items_found, items_saved)
        update_config_last_crawl(args.config_id)
    except Exception as e:
        update_log_error(args.log_id, str(e)); raise

if __name__ == "__main__": main()
