"""
Google Blog 新闻爬虫
=====================================

基于 RuoYi 舆情系统爬虫开发范式。
从 Google Blog 站点地图抓取最新文章。

使用方法：
  python google_blog_spider.py --max 3

爬虫流程：
  fetch_article_list() → 从站点地图收集文章链接
    → fetch_article_detail() → 获取每篇文章详情
      → 关键词过滤
        → save_news_article() → 保存到数据库
"""
import os, sys, re, time, random, argparse, hashlib  # 基础库
import xml.etree.ElementTree as ET  # XML解析库（用于解析站点地图）
from datetime import datetime  # 日期处理
from urllib.parse import urlparse  # URL处理

import requests  # HTTP请求库
from bs4 import BeautifulSoup  # HTML解析库

# ============================================================
# 导入统一配置和工具模块
# ============================================================
import crawler_config
from crawler_config import DB, IMAGE_DIR, MAX_ARTICLES, MAX_PAGES, REQUEST_TIMEOUT, REQUEST_DELAY, USER_AGENT, ALL_KEYWORDS, extract_keywords, contains_main_keyword
from proxy_config import PROXIES

HEADERS = {"User-Agent": USER_AGENT}
from content_utils import clean_content_html, remove_boilerplate_text
from common_db import (
    get_db, save_news_article,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)
from retry_utils import with_retry


# ============================================================
# 站点配置 — Google Blog
# ============================================================
SITE_NAME = "Google Blog"                                       # 站点名（写入 news_article.source 字段）
SITEMAP_URL = "https://blog.google/en-us/sitemap.xml"           # Google Blog 站点地图URL
IMAGE_DIR = IMAGE_DIR  # 图片目录（从 crawler_config 自动切换，一般无需修改）

# XML 命名空间（站点地图标准格式）
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ============================================================
# 工具函数
# ============================================================

def clean(text):
    """
    清理文本中的多余空白字符。
    """
    return re.sub(r"\s+", " ", text).strip() if text else ""


# ============================================================
# URL 过滤函数
# ============================================================

def is_article_url(url):
    """
    判断 URL 是否为 Google Blog 的文章页面。
    文章 URL 通常有 3 段以上路径，排除 authors/topics 等非文章路径。
    """
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3:
        return False
    # 排除非文章路径
    if parts[0] in {"authors", "topics", "image-library", "search", "about", "tag"}:
        return False
    return True


# ============================================================
# 图片下载函数
# ============================================================

def download_image(url, identifier, idx=0):
    """
    下载图片到本地目录。
    根据 identifier 生成唯一的本地文件名（MD5哈希前16位）。
    """
    if not url:
        return ""
    id_hash = hashlib.md5(identifier.encode()).hexdigest()[:16]
    filename = f"{id_hash}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        return os.path.relpath(local_path, os.path.dirname(IMAGE_DIR)).replace("\\", "/")
    try:
        resp = requests.get(url, proxies=PROXIES, timeout=30, stream=True)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        print(f"    [IMG] {filename} ({os.path.getsize(local_path)} bytes)")
        return os.path.relpath(local_path, os.path.dirname(IMAGE_DIR)).replace("\\", "/")
    except Exception as e:
        print(f"    [IMG] 下载失败: {e}")
        return ""


# ============================================================
# 日期提取函数
# ============================================================

def get_meta(soup, attrs):
    """
    从 meta 标签获取指定属性的内容。
    """
    tag = soup.find("meta", attrs=attrs)
    return tag.get("content", "").strip() if tag else ""


def get_final_date(sitemap_lastmod, soup):
    """
    提取文章发布日期，按优先级尝试多种来源：
    1. 页面中的日期 div
    2. 站点地图的 lastmod 字段
    3. 页面 meta 标签
    """
    # 1. 页面日期 div（Google Blog 特有格式）
    date_div = soup.find("div", class_="uni-body--small short-post__date")
    if date_div:
        raw_text = date_div.get_text(strip=True)
        pat = re.compile(r'([A-Za-z]{3} \d{1,2}, \d{4})')
        res = pat.search(raw_text)
        if res:
            return res.group(1)

    # 2. 站点地图 lastmod
    if sitemap_lastmod:
        try:
            dt = datetime.fromisoformat(sitemap_lastmod)
            return dt.strftime("%b %d, %Y")
        except Exception:
            return sitemap_lastmod

    # 3. 页面 meta 标签
    modified = get_meta(soup, {"property": "article:modified_time"})
    if modified:
        try:
            dt = datetime.fromisoformat(modified[:10])
            return dt.strftime("%b %d, %Y")
        except Exception:
            return modified

    published = (
        get_meta(soup, {"property": "article:published_time"})
        or get_meta(soup, {"itemprop": "datePublished"})
        or get_meta(soup, {"name": "publish-date"})
    )
    if published:
        try:
            dt = datetime.fromisoformat(published[:10])
            return dt.strftime("%b %d, %Y")
        except Exception:
            return published

    return ""


# ============================================================
# 列表页解析 — 站点地图
# ============================================================

def fetch_article_list(session, max_pages):
    """
    从 Google Blog 站点地图 XML 文件中提取文章链接。
    站点地图包含所有文章 URL 和 lastmod 日期。
    按 lastmod 降序排序，取最新的 max_pages * 10 篇。
    """
    articles = []  # 存储收集到的文章列表

    print(f"  [LIST] 站点地图: {SITEMAP_URL}")

    try:
        resp = with_retry(
            lambda: session.get(SITEMAP_URL, headers=HEADERS, proxies=PROXIES, timeout=REQUEST_TIMEOUT),
            description="Google Blog 站点地图"
        )
        root = ET.fromstring(resp.text)

        # 解析 XML 中的 url 元素
        for node in root.findall(".//sm:url", NS):
            loc = node.find("sm:loc", NS)
            if loc is None:
                continue
            url = loc.text.strip()

            # 过滤非文章 URL
            if not is_article_url(url):
                continue

            # 获取 lastmod 日期
            lm = node.find("sm:lastmod", NS)
            lastmod = lm.text.strip() if lm is not None else ""

            articles.append({"title": "", "url": url, "lastmod": lastmod})

        # 按 lastmod 降序排序（最新的在前）
        articles.sort(key=lambda x: x.get("lastmod", ""), reverse=True)

        # 取最新的一批（受 max_pages 限制）
        articles = articles[:max_pages * 10]

    except Exception as e:
        print(f"  [LIST] 失败: {e}")

    return articles


# ============================================================
# 文章详情解析 — Google Blog 文章页
# ============================================================

def fetch_article_detail(session, url, title, lastmod=""):
    """
    获取 Google Blog 文章详情页的内容。
    提取标题、日期、关键词、正文、封面图等信息。
    """
    try:
        resp = with_retry(
            lambda: session.get(url, headers=HEADERS, proxies=PROXIES, timeout=REQUEST_TIMEOUT),
            description=f"Google Blog 文章详情"
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        # 必须有 article 标签和 h1 标签
        article = soup.find("article")
        h1 = soup.find("h1")
        if not article or not h1:
            print("    [SKIP] 缺少 article 或 h1 标签")
            return None

        title = h1.get_text(strip=True)

        # 日期提取
        date = get_final_date(lastmod, soup)

        # 关键词提取：从页面标签中获取
        keywords = set()
        tag_elements = soup.find_all("a", class_="uni-blog-article-tags-value")
        for tag in tag_elements:
            keyword = tag.get_text(strip=True)
            if keyword and keyword not in {" ", "", "\n", "\t"}:
                keywords.add(keyword)

        # 回退到 meta 标签
        if not keywords:
            for tag in soup.find_all("meta", attrs={"property": "article:tag"}):
                c = tag.get("content", "").strip()
                if c:
                    keywords.add(c)

        # 如果页面标签中没有关键词，使用默认关键词列表匹配
        keywords_str = ",".join(sorted(keywords))
        if not keywords_str:
            keywords_str = extract_keywords(title)

        # 封面图：从 Open Graph 标签获取
        cover_url = (
            get_meta(soup, {"property": "og:image"})
            or get_meta(soup, {"name": "twitter:image"})
        )
        cover_path = ""
        if cover_url:
            cover_path = download_image(cover_url, url, idx=0)

        # 正文提取：清理 script/style 等标签后提取文本
        for bad in article(["script", "style", "svg", "noscript", "iframe"]):
            bad.extract()
        content = article.get_text("\n", strip=True)

        # 正文太短则跳过
        if not content or len(content) < 100:
            print("    [SKIP] 正文太短")
            return None

        return {
            "title": title,
            "url": url,
            "date": date,
            "keywords": keywords_str,
            "content": content,
            "cover_image": cover_path,
            "source": SITE_NAME,
        }

    except Exception as e:
        print(f"    [DETAIL] 失败: {e}")
        return None


# ============================================================
# 主流程
# ============================================================

def crawl(max_articles, max_pages, keyword=None):
    """
    爬虫主流程：站点地图 → 详情页 → 关键词过滤 → 入库。
    """
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
        print(f"  [{i+1}] {art['url'][:80]}")

        detail = fetch_article_detail(session, art["url"], art["title"], art.get("lastmod", ""))
        if not detail:
            continue
        if not contains_main_keyword(detail["title"] + " " + detail["content"]):
            print(f"    [SKIP] 无关键词")
            continue

        items_found += 1
        conn = get_db()
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
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。
    --config-id: 爬取配置ID（由调度系统传入）
    --keyword:   指定关键词（覆盖默认关键词列表）
    --max:       最多爬取的文章数（覆盖默认值 MAX_ARTICLES）
    --log-id:    爬取日志ID（由调度系统传入，用于更新日志）
    """
    parser = argparse.ArgumentParser(description=f"{SITE_NAME} Spider")
    parser.add_argument("--config-id", type=int, default=None)
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--log-id", type=int, default=None)
    return parser.parse_args()


def main():
    """
    主函数：解析参数 → 执行爬虫 → 更新日志。
    """
    args = parse_args()
    if args.keyword:
        crawler_config.ALL_KEYWORDS = [kw.strip() for kw in args.keyword.split(",") if kw.strip()]
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
