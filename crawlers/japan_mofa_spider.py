"""
Japan Ministry of Foreign Affairs (MOFA) Spider - Playwright
https://www.mofa.go.jp
"""
import os, sys, re, time, random, argparse
from crawler_config import DB, IMAGE_DIR
from playwright.sync_api import sync_playwright
from content_utils import extract_content_playwright, remove_boilerplate_text
from common_db import get_db, save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
import hashlib, requests as req_lib
from proxy_config import PROXIES, get_playwright_proxy
from retry_utils import with_retry_goto

SITE_NAME = "Japan MOFA"
BASE_URL = "https://www.mofa.go.jp"
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
    if meta.count() > 0: return meta.first.get_attribute("content") or ""
    meta2 = page.locator("meta[name='twitter:image']")
    if meta2.count() > 0: return meta2.first.get_attribute("content") or ""
    # Try first large image in article/main
    for sel in ["article img", "main img", ".entry-content img"]:
        img = page.locator(sel)
        if img.count() > 0:
            src = img.first.get_attribute("src") or ""
            if src and ("mofa" in src or src.startswith("http")):
                if src.startswith("/"): src = BASE_URL + src
                return src
    return ""

def extract_date(page):
    meta = page.locator("meta[property='article:published_time']")
    if meta.count() > 0: return meta.first.get_attribute("content") or ""
    t = page.locator("time")
    if t.count() > 0:
        dt = t.first.get_attribute("datetime")
        if dt: return dt
        return clean(t.first.inner_text())
    body_text = page.locator("body").inner_text()
    match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}", body_text)
    if match: return match.group(0)
    # Try Japanese date format: 2025年1月15日
    match2 = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", body_text)
    if match2: return f"{match2.group(1)}-{match2.group(2).zfill(2)}-{match2.group(3).zfill(2)}"
    return ""

def is_article_url(url):
    if not url.startswith(BASE_URL): return False
    bad = ["/whats/", "/about/", "/link/", "/sitemap/", "mailto:", "twitter.com", "#"]
    if any(x in url for x in bad): return False
    good = ["/press/", "/fp/", "/erp/", "/na/", "/sa/", "/me_a/", "/af/", "/ca/", "/ecm/",
            "/ic/", "/announce/", "/files/", "/region/", "/pressrelease/"]
    return any(x in url for x in good)

def normalize_url(href):
    if not href: return ""
    href = href.strip()
    if href.startswith("//"): return "https:" + href
    if href.startswith("/"): return BASE_URL + href
    return href

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

def crawl(max_pages, max_articles):
    """Crawl Japan MOFA news by month index pages."""
    items_found = 0
    items_new = 0
    items_updated = 0
    page_failures = 0
    visited_urls = set()
    conn = get_db()
    cur = conn.cursor()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, proxy=get_playwright_proxy(),
                args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                locale="en-US", viewport={"width": 1366, "height": 768})
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

            # Generate month index URLs for recent months
            from datetime import datetime, timedelta
            now = datetime.now()
            list_urls = []
            for i in range(max_pages):
                dt = now - timedelta(days=30 * i)
                list_urls.append(f"{BASE_URL}/whats/{dt.year}_index{dt.month:02d}.html")

            news_list = []
            for url in list_urls:
                try:
                    print(f"\n访问月度索引：{url}")
                    with_retry_goto(page, url, goto_kwargs={'wait_until': 'networkidle', 'timeout': 60000}, description=f"访问月度索引: {url[-30:]}")
                    time.sleep(2 + random.random())

                    html_check = page.content().lower()
                    if "access denied" in html_check:
                        print("被封禁"); continue

                    # Extract article links from various content areas
                    links = page.locator("main a[href], #maincontents a[href], #main_contents a[href], #contents a[href]")
                    print(f"链接数：{links.count()}")
                    for i in range(links.count()):
                        try:
                            href = normalize_url(links.nth(i).get_attribute("href"))
                            title = clean(links.nth(i).inner_text())
                            if not title or len(title) < 5: continue
                            if not is_article_url(href): continue
                            if href in visited_urls: continue
                            visited_urls.add(href)
                            news_list.append({"title": title, "url": href})
                            print(f"收录：{title}")
                        except: pass
                except Exception as e:
                    print(f"列表页失败：{e}")

            if page_failures > 0 and len(news_list) == 0:
                raise Exception(f"所有列表页访问失败({page_failures}次)，代理可能不可用")
            print(f"\n总文章数：{len(news_list)}")

            # === Detail pages ===
            for news in news_list:
                if (items_new + items_updated) >= max_articles: break
                try:
                    print(f"\n采集：{news['title']}")
                    with_retry_goto(page, news["url"], goto_kwargs={'wait_until': 'networkidle', 'timeout': 60000}, description=f"访问详情页: {news['title'][:30]}")
                    time.sleep(2 + random.random())

                    scroll_to_bottom(page)

                    # Title from page
                    for sel in ["h1", "h2", "h3"]:
                        h = page.locator(sel)
                        if h.count() > 0:
                            t = clean(h.first.inner_text())
                            if len(t) > 10 and "MOFA" not in t:
                                news["title"] = t; break

                    date = extract_date(page)
                    cover_url = extract_cover_image(page)
                    cover_image = download_image(cover_url, news["url"]) if cover_url else ""

                    # Content: try entry-content first, then main, fallback to body
                    content = extract_content_playwright(page, "article")
                    if not content:
                        for sel in ["div.entry-content", "div.article-body", "div.news-content"]:
                            el = page.locator(sel)
                            if el.count() > 0:
                                inner = el.first.inner_html()
                                from content_utils import clean_content_html
                                cleaned = clean_content_html(inner)
                                if cleaned and len(cleaned) > 100:
                                    content = cleaned; break
                    if not content:
                        # Extract p/li/h2/h3 from main
                        elements = page.locator("main p, main li, main h2, main h3, #maincontents p, #main_contents p, #contents p")
                        if elements.count() == 0:
                            elements = page.locator("body p, body li")
                        texts = []
                        for i in range(elements.count()):
                            try:
                                txt = clean(elements.nth(i).inner_text())
                                if len(txt) > 2: texts.append(f"<p>{txt}</p>")
                            except: pass
                        content = "\n".join(texts)
                    if not content:
                        content = remove_boilerplate_text(page.locator("body").first.inner_text())

                    items_found += 1
                    if not contains_keywords(content):
                        print(f"跳过(无关键词) content_len={len(content)}"); continue

                    keywords = extract_keywords(content)
                    article_data = {
                        "title": news["title"], "url": news["url"], "date": clean(date),
                        "content": content[:5000], "keywords": keywords,
                        "cover_image": "sentiment/images/" + cover_image if cover_image else "",
                        "source": SITE_NAME}
                    is_new, is_updated = save_news_article(cur, article_data)
                    if is_new: items_new += 1
                    elif is_updated: items_updated += 1
                    conn.commit()
                    if is_new: print("已保存(新增)")
                    elif is_updated: print("已保存(更新)")
                    else: print("已保存(无变化)")
                    time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"详情错误：{e}")
            browser.close()
    finally:
        cur.close(); conn.close()
    print(f"\n=== Done. New: {items_new}, Updated: {items_updated}, Total found: {items_found} ===")
    return items_found, items_new, items_updated

def parse_args():
    parser = argparse.ArgumentParser(description="Japan MOFA Spider")
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
        update_crawl_log_error(args.log_id, str(e)); raise

if __name__ == "__main__": main()
