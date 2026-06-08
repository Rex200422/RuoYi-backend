"""
Amnesty International News Spider - Playwright
"""
import os, sys, re, time, random, argparse
from crawler_config import DB, IMAGE_DIR
from playwright.sync_api import sync_playwright
from content_utils import extract_content_playwright, remove_boilerplate_text

import hashlib, requests as req_lib
from proxy_config import PROXIES, get_playwright_proxy
from retry_utils import with_retry_goto
from common_db import get_db, save_news_article, update_crawl_log_start, update_crawl_log, update_crawl_log_error, update_config_last_crawl


def download_image(url, article_id, idx=0):
    """下载图片到本地，返回本地文件名"""
    if not url:
        return ""
    aid_hash = hashlib.md5(str(article_id).encode()).hexdigest()[:16]
    filename = f"{aid_hash}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        return filename
    try:
        try:
            resp = req_lib.get(url, proxies=PROXIES, timeout=30)
        except Exception:
            resp = req_lib.get(url, timeout=30)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        print(f"    [IMG] 下载: {filename} ({len(resp.content)} bytes)")
        return filename
    except Exception as e:
        print(f"    [IMG] 下载失败: {e}")
        return ""



SITE_NAME = "Amnesty"
BASE_URL = "https://www.amnesty.org"
DEFAULT_MAX_PAGES = 2
DEFAULT_MAX_ARTICLES = 2

MAIN_KEYWORDS = ["china", "taiwan"]
SUB_KEYWORDS = ["trade", "technology", "military", "sanctions", "indo-pacific", "south china sea",
                "semiconductor", "cyber", "beijing", "human rights", "xinjiang", "hong kong",
                "uyghur", "tibet", "ccp"]

def clean(text): return re.sub(r"\s+", " ", text).strip() if text else ""

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

COOKIE_TITLES = ["your choice regarding cookies", "cookie", "privacy policy", "cookie policy", "terms of use"]

def is_valid_article(url):
    if not url.startswith(BASE_URL): return False
    bad = ["/search/", "/campaigns/", "/take-action/", "/donate/", "/contact/", "/petition/"]
    return not any(x in url for x in bad)

def is_cookie_title(title):
    """Check if title is from cookie consent banner."""
    t = title.lower().strip()
    return any(ct in t for ct in COOKIE_TITLES)

def crawl(max_pages, max_articles):
    items_found = 0
    items_new = 0
    items_updated = 0
    page_failures = 0
    visited_urls = set()
    conn = get_db()
    cur = conn.cursor()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, proxy=get_playwright_proxy(),
                                        args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--disable-background-networking", "--no-sandbox"])
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
                    with_retry_goto(list_page, url, goto_kwargs={'timeout': 60000}, description=f"访问列表页{page_num}")
                    list_page.wait_for_load_state("networkidle")
                    try: list_page.locator("button:has-text('ACCEPT')").click(timeout=3000)
                    except: pass
                except Exception as e:
                    print(f"页面失败：{e}")
                    page_failures += 1
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
            if page_failures > 0 and len(news_list) == 0:
                raise Exception(f"所有列表页访问失败({page_failures}次)，代理可能不可用")
            print(f"\n总文章数：{len(news_list)}")

            for news in news_list:
                if items_new + items_updated >= max_articles:
                    break
                try:
                    print(f"\n采集：{news['title']}")
                    detail_page = context.new_page()
                    with_retry_goto(detail_page, news["url"], goto_kwargs={'timeout': 60000}, description=f"访问详情页: {news['title'][:30]}")
                    detail_page.wait_for_load_state("networkidle")
                    try: detail_page.locator("button:has-text('ACCEPT')").click(timeout=3000)
                    except: pass
                    detail_page.wait_for_timeout(3000)
                    try: detail_page.locator("button:has-text('ACCEPT')").click(timeout=5000)
                    except: pass
                    detail_page.wait_for_timeout(1000)
                    h1 = detail_page.locator("article h1")
                    if h1.count() == 0:
                        h1 = detail_page.locator("h1")
                    title = clean(h1.first.inner_text()) if h1.count() > 0 else clean(detail_page.title())
                    if is_cookie_title(title):
                        title = clean(detail_page.title())
                    date = extract_date(detail_page)
                    cover_url = extract_cover_image(detail_page)
                    cover_image = download_image(cover_url, news["url"]) if cover_url else ""
                    article_el = detail_page.locator("article")
                    content = extract_content_playwright(detail_page, "article") if article_el.count() > 0 else ""
                    if not content and article_el.count() > 0:
                        content = remove_boilerplate_text(article_el.first.inner_text())
                    content = clean(content)
                    detail_page.close()
                    if len(content) < 300:
                        print("正文太短")
                        continue
                    items_found += 1
                    if not contains_keywords(content):
                        print(f"跳过：无 China/Taiwan content_len={len(content)}")
                        continue
                    keywords = extract_keywords(content)
                    article_data = {"title": title, "url": news["url"], "date": date,
                                    "content": content[:5000], "keywords": keywords,
                                    "cover_image": "sentiment/images/" + cover_image if cover_image else "", "source": SITE_NAME}
                    is_new, is_updated = save_news_article(cur, article_data)
                    conn.commit()
                    if is_new:
                        items_new += 1
                    if is_updated:
                        items_updated += 1
                    print("已保存")
                    time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"详情错误：{e}")
            browser.close()
    finally:
        cur.close()
        conn.close()
    print(f"\n=== Done. New: {items_new}, Updated: {items_updated} ===")
    return items_found, items_new, items_updated

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
    update_crawl_log_start(args.log_id)
    try:
        items_found, items_new, items_updated = crawl(DEFAULT_MAX_PAGES, max_articles)
        update_crawl_log(args.log_id, items_found, items_new, items_updated)
        update_config_last_crawl(args.config_id)
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))
        raise

if __name__ == "__main__":
    main()
