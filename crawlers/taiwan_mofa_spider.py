"""
Taiwan Ministry of Foreign Affairs (MOFA) Spider - Playwright
https://en.mofa.gov.tw
"""
import os, sys, re, time, random, argparse
from crawler_config import DB, IMAGE_DIR
from playwright.sync_api import sync_playwright
from content_utils import extract_content_playwright, remove_boilerplate_text
from common_db import get_db, save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
import hashlib, requests as req_lib
from proxy_config import PROXIES, get_playwright_proxy

SITE_NAME = "Taiwan MOFA"
BASE_URL = "https://en.mofa.gov.tw"
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
    # Try main images
    for sel in ["article img", ".cp img", "main img", ".news-content img"]:
        img = page.locator(sel)
        if img.count() > 0:
            src = img.first.get_attribute("src") or ""
            if src and (".jpg" in src.lower() or ".png" in src.lower() or "image" in src.lower()):
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
    return ""

def extract_title(page):
    for sel in [".cp h3", ".cp h1", "h1", "h2", "h3"]:
        loc = page.locator(sel)
        if loc.count() > 0:
            text = clean(loc.first.inner_text())
            if len(text) > 15 and "Ministry of Foreign Affairs" not in text:
                return text
    return ""

def get_page_url(page_num):
    base = (f"{BASE_URL}/News.aspx?n=1328&sms=273&_Query=dede8dfc-eaec-4092-8c7c-86725b2ea57d")
    if page_num == 1: return base
    return base + f"&page={page_num}&PageSize=20"

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
                locale="en-US")
            page = context.new_page()

            news_list = []
            # === List pages ===
            for page_num in range(1, max_pages + 1):
                url = get_page_url(page_num)
                print(f"\n访问：{url}")
                try:
                    page.goto(url, timeout=60000)
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)
                except Exception as e:
                    print(f"列表页失败：{e}"); page_failures += 1; continue

                links = page.locator("a[href*='News_Content.aspx']")
                print(f"链接数：{links.count()}")
                for i in range(links.count()):
                    try:
                        item = links.nth(i)
                        href = item.get_attribute("href") or ""
                        title = clean(item.inner_text())
                        if not href: continue
                        if len(title) < 8: continue
                        if href.startswith("/"): href = BASE_URL + href
                        elif not href.startswith("http"): href = BASE_URL + "/" + href
                        if "News_Content.aspx" not in href: continue
                        if href in visited_urls: continue
                        visited_urls.add(href)
                        news_list.append({"title": title, "url": href})
                        print(f"收录：{title}")
                    except: pass

            if page_failures > 0 and len(news_list) == 0:
                raise Exception(f"所有列表页访问失败({page_failures}次)，代理可能不可用")
            print(f"\n总文章数：{len(news_list)}")

            # === Detail pages ===
            for news in news_list:
                if (items_new + items_updated) >= max_articles: break
                try:
                    print(f"\n采集：{news['title']}")
                    detail_page = context.new_page()
                    detail_page.goto(news["url"], timeout=60000)
                    detail_page.wait_for_load_state("domcontentloaded")
                    time.sleep(1)

                    scroll_to_bottom(detail_page)

                    # Title
                    title = extract_title(detail_page)
                    if not title: title = news["title"]

                    # Content
                    content = extract_content_playwright(detail_page, "article")
                    if not content:
                        # Taiwan MOFA uses <p> tags directly
                        elements = detail_page.locator("p")
                        texts = []
                        for i in range(elements.count()):
                            try:
                                txt = clean(elements.nth(i).inner_text())
                                if len(txt) > 20: texts.append(f"<p>{txt}</p>")
                            except: pass
                        content = "\n".join(texts)
                    if not content:
                        content = remove_boilerplate_text(detail_page.locator("body").first.inner_text())

                    # Date
                    date = extract_date(detail_page)

                    # Cover image
                    cover_url = extract_cover_image(detail_page)
                    cover_image = download_image(cover_url, news["url"]) if cover_url else ""

                    detail_page.close()

                    if len(content) < 300:
                        print("跳过(正文太短)"); continue

                    items_found += 1
                    if not contains_keywords(content):
                        print(f"跳过(无关键词) content_len={len(content)}"); continue

                    keywords = extract_keywords(content)
                    article_data = {
                        "title": title, "url": news["url"], "date": clean(date),
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
    parser = argparse.ArgumentParser(description="Taiwan MOFA Spider")
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
