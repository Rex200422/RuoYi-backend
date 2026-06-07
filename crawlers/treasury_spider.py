"""
U.S. Treasury News Press Releases Spider v3
Fixes:
  - Date: extract from <time class='datetime'> (first in body, skip banner/header)
  - Content: extract from <meta property='og:description'> (full article text)
  - URLs: use short slugs from listing page as-is
"""
import sys
import re
import time
import random
import argparse
import warnings
from content_utils import clean_content_html

import requests
from bs4 import BeautifulSoup
import pymysql
from common_db import save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
from crawler_config import DB, USER_AGENT
from common_db import get_db

warnings.filterwarnings("ignore")

# --------------- config ---------------
from proxy_config import PROXIES, get_playwright_proxy
HEADERS = {"User-Agent": USER_AGENT}

MAIN_KEYWORDS = ["china", "taiwan"]
SUB_KEYWORDS = [
    "trade", "technology", "military", "sanctions",
    "tariff", "investment", "financial",
]
ALL_KEYWORDS = MAIN_KEYWORDS + SUB_KEYWORDS

DEFAULT_MAX_PAGES = 3
DEFAULT_MAX_ARTICLES = 2
BASE_URL = "https://home.treasury.gov/news/press-releases"

# --------------- helpers ---------------


def clean(text):
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text).strip()


def strip_html_tags(html_str):
    """Remove all HTML tags and decode entities, returning plain text."""
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    return clean(soup.get_text(" ", strip=True))


def extract_kw(text):
    """Return comma-separated matched keywords."""
    t = text.lower()
    return ",".join(sorted(
        k for k in ALL_KEYWORDS
        if re.search(rf"\b{k.replace('-', '[- ]')}\b", t)
    ))


def matches_main_keywords(title, content):
    """Return True if title or content contains any main keyword."""
    combined = (title + " " + content).lower()
    return any(kw in combined for kw in MAIN_KEYWORDS)



# --------------- extraction ---------------

def extract_og_description(soup, url=None):
    """
    Extract article content preserving paragraph structure.
    Priority: Playwright rendered text > og:description with smart splitting
    """
    # 方法1: 尝试从main/section标签获取（JS渲染后）
    nav_keywords = ["role of the treasury", "officials", "organizational",
        "domestic finance", "economic policy", "general counsel", "international affairs",
        "management", "public affairs", "orders and directives", "terrorism",
        "enter search", "about treasury", "general information", "inspectors general"]
    for tag_name in ["main", "article", "section"]:
        tag = soup.find(tag_name)
        if tag:
            text = tag.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 10]
            # 过滤导航内容
            filtered = [l for l in lines if not any(kw in l.lower() for kw in nav_keywords)]
            # 必须有至少5段非导航内容，且包含WASHINGTON或长文本
            has_article = any("WASHINGTON" in l or len(l) > 100 for l in filtered)
            if len(filtered) > 5 and has_article:
                # Convert text lines to HTML for cleaning
                text_html = "\n".join(f"<p>{l}</p>" for l in filtered)
                cleaned = clean_content_html(text_html)
                if cleaned:
                    return cleaned
                return "\n".join(filtered)

    # 方法2: Playwright获取渲染后的段落（如果URL提供）
    if url:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, proxy=get_playwright_proxy(), args=["--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--no-sandbox"])
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
                body_text = page.evaluate("() => document.body.innerText")
                browser.close()

                lines = [l.strip() for l in body_text.split("\n") if l.strip()]
                # 找正文起始（WASHINGTON开头或日期后）
                start = -1
                for i, line in enumerate(lines):
                    if line.startswith("WASHINGTON") or line.startswith("NEW YORK") or re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}", line):
                        start = i
                        break
                if start > -1:
                    end = len(lines)
                    for i in range(start + 5, len(lines)):
                        if lines[i].strip() in ("###", "---") or "Contact:" in lines[i] or "Media Contact" in lines[i]:
                            end = i
                            break
                    article = lines[start:end]
                    if len(article) > 3:
                        text_html = "\n".join(f"<p>{l}</p>" for l in article)
                        cleaned = clean_content_html(text_html)
                        if cleaned:
                            return cleaned
                        return "\n".join(article)
        except Exception:
            pass

    # 方法3: meta description fallback
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return strip_html_tags(meta["content"])
    return ""


def extract_article_date(soup):
    """
    Extract the article date from the first <time class='datetime'> element
    that is inside the article body — NOT from the banner, navigation, or
    header area.

    The America 250th Anniversary banner contains a <time> with
    'July 4, 2026'; we skip any <time> whose text contains '2026' and
    sits inside a header/banner/nav element.
    """
    # Find all <time class='datetime'> tags with a datetime attribute
    time_tags = soup.find_all("time", class_="datetime")
    if not time_tags:
        # broader fallback: any <time> with datetime attr
        time_tags = soup.find_all("time", attrs={"datetime": True})

    # Skip zones: header, nav, footer, banner, .hero, .announcement
    skip_parents = {"header", "nav", "footer", "banner"}
    skip_classes = {"hero", "banner", "announcement", "am250"}

    for t_tag in time_tags:
        # Check if this <time> is inside a skip zone
        parent = t_tag.parent
        in_skip = False
        depth = 0
        while parent and depth < 10:
            tag_name = parent.name or ""
            tag_class = " ".join(parent.get("class", []))
            if tag_name in skip_parents or any(
                sc in tag_class.lower() for sc in skip_classes
            ):
                in_skip = True
                break
            parent = parent.parent
            depth += 1

        if in_skip:
            continue

        # Extract the datetime attribute (ISO format)
        dt_attr = t_tag.get("datetime", "")
        # Also grab the visible text
        dt_text = clean(t_tag.get_text())

        # Skip obvious banner dates (e.g. "July 4, 2026" for America 250)
        if dt_text and "2026" in dt_text:
            # Check if this is a banner-like date — if the datetime attr
            # matches July 4, 2026, skip it
            if "07-04" in dt_attr or "2026-07-04" in dt_attr:
                continue

        # Prefer the visible text as it's human-readable
        if dt_text:
            return dt_text
        if dt_attr:
            return dt_attr

    return ""


# --------------- main crawl ---------------

def crawl(max_pages, max_articles):
    """Core crawl logic. Returns (items_found, items_new, items_updated) counts."""
    print("=== U.S. Treasury Spider v3 ===")
    session = requests.Session()
    conn = get_db()
    cur = conn.cursor()
    count = 0
    items_found = 0
    items_new = 0
    items_updated = 0
    visited = set()

    try:
        for pg in range(1, max_pages + 1):
            if count >= max_articles:
                break

            url = BASE_URL if pg == 1 else f"{BASE_URL}?page={pg}"
            print(f"\nPage {pg}: {url}")

            try:
                r = session.get(
                    url, headers=HEADERS, proxies=PROXIES,
                    timeout=30, verify=False,
                )
                soup = BeautifulSoup(r.text, "html.parser")

                # Find article links — they point to /news/press-releases/<slug>
                links = soup.select("a[href*='/news/press-releases/']")
                # Deduplicate while preserving order
                seen = set()
                unique_links = []
                for a in links:
                    href = a.get("href", "").strip()
                    if not href or "/news/press-releases/" not in href:
                        continue
                    if href.startswith("/"):
                        href = "https://home.treasury.gov" + href
                    if href in seen:
                        continue
                    seen.add(href)
                    unique_links.append(a)

                print(f"  Found {len(unique_links)} article links")

                for a in unique_links:
                    if count >= max_articles:
                        break

                    href = a.get("href", "").strip()
                    if href.startswith("/"):
                        href = "https://home.treasury.gov" + href

                    if href in visited:
                        continue
                    visited.add(href)

                    title = clean(a.get_text())
                    if len(title) < 5:
                        continue

                    # Fetch detail page
                    try:
                        r2 = session.get(
                            href, headers=HEADERS, proxies=PROXIES,
                            timeout=30, verify=False,
                        )
                        s2 = BeautifulSoup(r2.text, "html.parser")
                        content = extract_og_description(s2, url=href)
                        date = extract_article_date(s2)
                    except Exception as e:
                        print(f"    [WARN] Detail fetch failed: {e}")
                        content = ""
                        date = ""

                    # 内容获取后，再做关键词过滤（标题+内容都检查）
                    if not matches_main_keywords(title, content):
                        print(f"    Skip (no keyword): {title[:50]}")
                        continue

                    items_found += 1

                    # 将纯文本转成HTML段落
                    if "\n" in content:
                        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
                        content = "\n".join(f"<p>{p}</p>" for p in paragraphs)

                    # Store
                    keywords_str = extract_kw(title + " " + content)
                    article = {"title": title, "url": href, "date": date, "keywords": keywords_str, "content": content, "source": "U.S. Treasury"}
                    is_new, is_updated = save_news_article(cur, article)
                    if is_new: items_new += 1
                    if is_updated: items_updated += 1
                    conn.commit()
                    count += 1
                    print(
                        f"    [{count}] {title[:60]}\n"
                        f"      Date: {date or '(none)'}\n"
                        f"      Content length: {len(content)} chars\n"
                        f"      Keywords: {keywords_str}"
                    )
                    time.sleep(random.uniform(1.5, 3))

            except Exception as e:
                print(f"  [ERR] Page {pg} failed: {e}")

    finally:
        cur.close()
        conn.close()

    print(f"\n=== Done. Total saved: {count} articles ===")
    return items_found, items_new, items_updated


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="U.S. Treasury Spider")
    parser.add_argument("--config-id", type=int, default=None, help="crawl_config ID")
    parser.add_argument("--keyword", type=str, default=None, help="(reserved for logging; Treasury uses built-in keyword list)")
    parser.add_argument("--max", type=int, default=None, help="Max articles to crawl (overrides default)")
    parser.add_argument("--log-id", type=int, default=None, help="crawl_log ID to update")
    # Backward compat: allow positional args as max_pages and max_articles
    parser.add_argument("max_pages_legacy", nargs="?", type=int, default=None, help="(legacy) max pages")
    parser.add_argument("max_articles_legacy", nargs="?", type=int, default=None, help="(legacy) max articles")
    return parser.parse_args()

def main():
    args = parse_args()

    # Determine max pages / max articles
    max_articles = args.max if args.max is not None else (args.max_articles_legacy if args.max_articles_legacy is not None else DEFAULT_MAX_ARTICLES)
    max_pages = args.max_pages_legacy if args.max_pages_legacy is not None else DEFAULT_MAX_PAGES

    config_id = args.config_id
    log_id = args.log_id

    # On start: update log start_time
    update_crawl_log_start(log_id)

    try:
        items_found, items_new, items_updated = crawl(max_pages, max_articles)
        # On success: update log and config
        update_crawl_log(log_id, items_found, items_new, items_updated)
        update_config_last_crawl(config_id)
    except Exception as e:
        # On error: update log with error
        update_crawl_log_error(log_id, str(e))
        raise

if __name__ == "__main__":
    main()
