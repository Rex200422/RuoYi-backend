"""
White House News Spider - requests直连版
https://www.whitehouse.gov/news/

白宫CDN(Cloudflare)屏蔽代理出口的TLS连接，需直连。
使用TLSAdapter降级SSL安全级别以兼容白宫服务器。
"""
import os, sys, re, time, random, argparse, hashlib
from crawler_config import DB, IMAGE_DIR
from common_db import get_db, save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
import requests as req_lib
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


# ============================================================
# 站点配置
# ============================================================
SITE_NAME = "White House"                    # 站点名（写入 news_article.source）
BASE_URL = "https://www.whitehouse.gov"     # 列表页基础 URL
DEFAULT_MAX_PAGES = 2                        # 列表页最多翻页数
DEFAULT_MAX_ARTICLES = 2                     # 每次最多爬取文章数

# 关键词：用于过滤相关文章
MAIN_KEYWORDS = ["china", "taiwan"]
SUB_KEYWORDS = ["trade", "technology", "military", "sanctions", "indo-pacific", "south china sea",
                "semiconductor", "cyber", "beijing", "human rights", "xinjiang", "hong kong",
                "uyghur", "tibet", "ccp"]


# ============================================================
# SSL/TLS 适配器（白宫CDN需要降低SSL安全级别才能直连）
# ============================================================
class TLSAdapter(HTTPAdapter):
    """降级SSL安全级别，解决白宫Cloudflare CDN的TLS兼容问题"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


# ============================================================
# 工具函数
# ============================================================
def clean(text):
    """清理文本中的多余空白字符"""
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_keywords(text):
    """从文本中提取匹配的关键词列表"""
    t = text.lower()
    return ",".join(sorted(set(
        k for k in MAIN_KEYWORDS + SUB_KEYWORDS
        if re.search(rf"\b{re.escape(k)}\b", t)
    )))


def contains_keywords(text):
    """检查文本是否包含主要关键词（用于过滤无关文章）"""
    t = text.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in MAIN_KEYWORDS)


def create_session():
    """创建带TLS适配器的requests会话（直连，不走代理）"""
    session = req_lib.Session()
    session.mount('https://', TLSAdapter())
    session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0'
    return session


def download_image(url, article_id, idx=0):
    """下载封面图片到本地存储，返回本地文件名"""
    if not url:
        return ""
    try:
        fname = f"{hashlib.md5(article_id.encode()).hexdigest()[:16]}_{idx}.jpg"
        path = os.path.join(IMAGE_DIR, fname)
        if os.path.exists(path):
            return fname
        os.makedirs(IMAGE_DIR, exist_ok=True)
        session = create_session()
        r = session.get(url, timeout=30, proxies={'http': None, 'https': None})
        if r.status_code == 200 and len(r.content) > 1000:
            with open(path, "wb") as f:
                f.write(r.content)
            return fname
    except Exception as e:
        print(f"  [IMG] 下载失败: {e}")
    return ""


# ============================================================
# 数据采集
# ============================================================
def fetch_list_page(session, page_num):
    """
    获取列表页，解析文章链接。
    返回: [{"title": str, "url": str, "date": str}, ...]
    """
    url = f"{BASE_URL}/news/" if page_num == 1 else f"{BASE_URL}/news/page/{page_num}/"
    print(f"\n访问：{url}")
    try:
        r = session.get(url, timeout=20, proxies={'http': None, 'https': None})
        soup = BeautifulSoup(r.text, 'html.parser')
        items = soup.select('li.wp-block-post')
        print(f"文章块：{len(items)}")
        return items
    except Exception as e:
        print(f"列表页失败：{e}")
        return []


def parse_list_item(item):
    """从列表项中提取标题、URL和日期"""
    a = item.select_one('h2 a, h3 a')
    if not a:
        return None
    title = clean(a.get_text(strip=True))
    href = a.get('href', '')
    if not href or len(title) < 5:
        return None
    date = ""
    date_el = item.select_one('time')
    if date_el:
        date = clean(date_el.get_text(strip=True))
    return {"title": title, "url": href, "date": date}


def fetch_detail(session, url):
    """
    获取详情页，解析正文内容。
    返回: {"content": str, "cover_image": str, "date": str} 或 None
    """
    r = session.get(url, timeout=20, proxies={'http': None, 'https': None})
    soup = BeautifulSoup(r.text, 'html.parser')

    # 提取正文：main标签中的p/h2/h3/li
    main = soup.select_one('main')
    if not main:
        return None

    elements = main.select('p, h2, h3, li')
    texts = []
    for el in elements:
        t = clean(el.get_text(strip=True))
        if len(t) > 2:
            if el.name in ('h2', 'h3'):
                texts.append(f"<h3>{t}</h3>")
            else:
                texts.append(f"<p>{t}</p>")
    content = "\n".join(texts)

    # 提取封面图片
    cover = ""
    og = soup.select_one("meta[property='og:image']")
    if og:
        cover = og.get('content', '')
    if not cover:
        tw = soup.select_one("meta[name='twitter:image']")
        if tw:
            cover = tw.get('content', '')

    # 提取日期
    date = ""
    pub = soup.select_one("meta[property='article:published_time']")
    if pub:
        date = pub.get('content', '')
    if not date:
        time_el = soup.select_one("time[datetime]")
        if time_el:
            date = time_el.get('datetime', '')

    return {"content": content, "cover_image": cover, "date": date}


# ============================================================
# 主爬取逻辑
# ============================================================
def crawl(max_pages, max_articles):
    """
    爬取白宫新闻。
    返回: (items_found, items_new, items_updated)
    """
    items_found = 0
    items_new = 0
    items_updated = 0
    page_failures = 0
    visited_urls = set()
    conn = get_db()
    cur = conn.cursor()

    try:
        session = create_session()

        # === 获取列表页 ===
        news_list = []
        for page_num in range(1, max_pages + 1):
            items = fetch_list_page(session, page_num)
            if not items:
                page_failures += 1
                continue
            for item in items:
                parsed = parse_list_item(item)
                if parsed and parsed["url"] not in visited_urls:
                    visited_urls.add(parsed["url"])
                    news_list.append(parsed)
                    print(f"收录：{parsed['title']}")

        if page_failures > 0 and not news_list:
            raise Exception(f"所有列表页访问失败({page_failures}次)，代理可能不可用")
        print(f"\n总文章数：{len(news_list)}")

        # === 获取详情页 ===
        for news in news_list:
            if (items_new + items_updated) >= max_articles:
                break
            try:
                print(f"\n采集：{news['title']}")
                time.sleep(random.uniform(1, 3))

                detail = fetch_detail(session, news["url"])
                if not detail:
                    print("详情获取失败"); continue

                date = news.get("date", "") or detail.get("date", "")
                cover_url = detail.get("cover_image", "")
                cover_image = download_image(cover_url, news["url"]) if cover_url else ""

                content = detail["content"]
                items_found += 1

                if not contains_keywords(content):
                    print(f"跳过(无关键词) content_len={len(content)}")
                    continue

                keywords = extract_keywords(content)
                article_data = {
                    "title": news["title"],
                    "url": news["url"],
                    "date": clean(date),
                    "content": content,
                    "keywords": keywords,
                    "cover_image": "sentiment/images/" + cover_image if cover_image else "",
                    "source": SITE_NAME,
                }
                is_new, is_updated = save_news_article(cur, article_data)
                if is_new:
                    items_new += 1
                elif is_updated:
                    items_updated += 1
                conn.commit()
                if is_new:
                    print("已保存(新增)")
                elif is_updated:
                    print("已保存(更新)")
                else:
                    print("已保存(无变化)")
            except Exception as e:
                print(f"详情错误：{e}")
    finally:
        cur.close()
        conn.close()

    print(f"\n=== Done. New: {items_new}, Updated: {items_updated}, Total found: {items_found} ===")
    return items_found, items_new, items_updated


# ============================================================
# 命令行入口
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(description="White House News Spider (requests直连版)")
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
