"""
Amnesty International News Spider - Playwright
"""
import os, sys, re, time, random, argparse
from playwright.sync_api import sync_playwright
import pymysql

PROXY = "http://192.168.0.14:7890/"
DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
SITE_NAME = "Amnesty"
BASE_URL = "https://www.amnesty.org"
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
    VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE content=VALUES(content), keywords=VALUES(keywords), cover_image=VALUES(cover_image)"""
    cursor.execute(sql, (article["title"], article["url"], article["date"], article["keywords"], article.get("cover_image", ""), article["content"], article["source"]))

def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted(set(k for k in MAIN_KEYWORDS + SUB_KEYWORDS if re.search(rf"\b{re.escape(k)}\b", t))))

def contains_keywords(text):
    t = text.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in MAIN_KEYWORDS)

def extract_cover_image(page):
    meta = page.locator("meta[property='og:image']")
    if meta.count() > 0:
        return meta.first.get_attribute("content") or ""
    meta2 = page.locator("meta[name='twitter:image']")
    if meta2.count() > 0:
        return meta2.first.get_attribute("content") or ""
    return ""

def get_page_url(page_num):
    if page_num == 1:
        return "https://www.amnesty.org/en/location/asia-and-the-pacific/east-asia/china/"
    return f"https://www.amnesty.org/en/location/asia-and-the-pacific/east-asia/china/page/{page_num}/"

def extract_date(page):
    meta = page.locator("meta[property='article:published_time']")
    if meta.count() > 0: return meta.first.get_attribute("content") or ""
    t = page.locator("time")
    if t.count() > 0:
        dt = t.first.get_attribute("datetime")
        if dt: return dt
        return clean(t.first.inner_text())
    return ""

def is_valid_article(url):
    if not url.startswith(BASE_URL): return False
    bad = ["/search/", "/campaigns/", "/take-action/", "/donate/", "/contact/", "/petition/"]
    return not any(x in url for x in bad)

def crawl(max_pages, max_articles):
    items_found = 0
    items_saved = 0
    visited_urls = set()
    conn = get_db()
    cur = conn.cursor()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, proxy={"server": PROXY},
                                        args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                viewport={"width": 1400, "height": 900}, locale="en-US")
            list_page = context.new_page()
            list_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            news_list = []
            for page_num in range(1, max_pages + 1):
                url = get_page_url(page_num)
                print(f"\n访问：{url}")
                try:
                    list_page.goto(url, timeout=60000)
                    list_page.wait_for_load_state("networkidle")
                    try: list_page.locator("button:has-text('Accept all')").click(timeout=3000)
                    except: pass
                except Exception as e:
                    print(f"页面失败：{e}")
                    continue
                news_section = list_page.locator("section#news")
                if news_section.count() == 0:
                    print("没有news section")
                    continue
                articles = news_section.locator("article")
                print(f"新闻数：{articles.count()}")
                for i in range(articles.count()):
                    try:
                        item = articles.nth(i)
                        links = item.locator("a")
                        for j in range(links.count()):
                            a = links.nth(j)
                            href = a.get_attribute("href")
                            title = clean(a.inner_text())
                            if href:
                                if href.startswith("/"): href = BASE_URL + href
                                if len(title) > 15 and "/en/latest/" in href and is_valid_article(href) and href not in visited_urls:
                                    visited_urls.add(href)
                                    news_list.append({"title": title, "url": href})
                                    print(f"收录：{title}")
                                    break
                    except Exception as e:
                        print(f"列表文章错误：{e}")
            print(f"\n总文章数：{len(news_list)}")

            for news in news_list:
                if items_saved >= max_articles:
                    break
                try:
                    items_found += 1
                    print(f"\n采集：{news['title']}")
                    detail_page = context.new_page()
                    detail_page.goto(news["url"], timeout=60000)
                    detail_page.wait_for_load_state("networkidle")
                    try: detail_page.locator("button:has-text('Accept all')").click(timeout=3000)
                    except: pass
                    h1 = detail_page.locator("h1")
                    title = clean(h1.first.inner_text()) if h1.count() > 0 else clean(detail_page.title())
                    date = extract_date(detail_page)
                    cover_image = extract_cover_image(detail_page)
                    article_el = detail_page.locator("article")
                    content = clean(article_el.first.inner_text()) if article_el.count() > 0 else ""
                    detail_page.close()
                    if len(content) < 300:
                        print("正文太短")
                        continue
                    if not contains_keywords(content):
                        print("跳过：无 China/Taiwan")
                        continue
                    keywords = extract_keywords(content)
                    article_data = {"title": title, "url": news["url"], "date": date,
                                    "content": content[:5000], "keywords": keywords,
                                    "cover_image": cover_image, "source": SITE_NAME}
                    save_article(cur, article_data)
                    conn.commit()
                    items_saved += 1
                    print("已保存")
                    time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"详情错误：{e}")
            browser.close()
    finally:
        cur.close()
        conn.close()
    print(f"\n=== Done. Total saved: {items_saved} articles ===")
    return items_found, items_saved

def parse_args():
    parser = argparse.ArgumentParser(description="Amnesty Spider")
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
        update_log_error(args.log_id, str(e))
        raise

if __name__ == "__main__":
    main()
