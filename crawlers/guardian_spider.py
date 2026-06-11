"""
Guardian 新闻爬虫
=====================================

基于 RuoYi 舆情系统爬虫开发范式。
从 The Guardian 中国板块抓取新闻文章。

使用方法：
  python guardian_spider.py --max 3

爬虫流程：
  fetch_article_list() → 收集文章链接
    → fetch_article_detail() → 获取每篇文章详情
      → 关键词过滤
        → save_news_article() → 保存到数据库
"""
import os, sys, re, time, random, argparse, hashlib, json  # 基础库

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
# 站点配置 — Guardian 中国板块
# ============================================================
SITE_NAME = "Guardian"                                          # 站点名（写入 news_article.source 字段）
BASE_URL = "https://www.theguardian.com/world/china"            # 列表页URL（Guardian中国板块）
IMAGE_DIR = IMAGE_DIR  # 图片目录（从 crawler_config 自动切换，一般无需修改）


# ============================================================
# 工具函数
# ============================================================

def clean(text):
    """
    清理文本中的多余空白字符。
    """
    return re.sub(r"\s+", " ", text).strip() if text else ""


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
        resp = requests.get(url, proxies=PROXIES, timeout=30)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        print(f"    [IMG] {filename} ({len(resp.content)} bytes)")
        return os.path.relpath(local_path, os.path.dirname(IMAGE_DIR)).replace("\\", "/")
    except Exception as e:
        print(f"    [IMG] 下载失败: {e}")
        return ""


# ============================================================
# 列表页解析 — Guardian 中国板块
# ============================================================

def fetch_article_list(session, max_pages):
    """
    从 Guardian 中国板块列表页收集文章链接。
    使用 a[data-link-name*='card'] 选择器定位文章卡片。
    """
    articles = []  # 存储收集到的文章列表
    visited = set()  # 已访问的URL集合，用于去重

    for page_num in range(1, max_pages + 1):
        # Guardian 分页格式: /world/china?page=2
        url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
        print(f"  [LIST] Page {page_num}: {url}")

        try:
            # 使用 retry_utils 包装请求
            resp = with_retry(
                lambda: session.get(url, headers=HEADERS, proxies=PROXIES, timeout=REQUEST_TIMEOUT),
                description=f"Guardian列表页第{page_num}页"
            )
            soup = BeautifulSoup(resp.text, "html.parser")

            # Guardian 的文章卡片使用 data-link-name 属性
            for a_tag in soup.select("a[data-link-name*='card']"):
                href = a_tag.get("href", "").strip()
                if not href:
                    continue

                # 过滤直播链接（/live/ 路径）
                if "/live/" in href:
                    continue

                # Guardian 特有：优先使用 aria-label 作为标题
                title = a_tag.get("aria-label") or clean(a_tag.get_text())

                if not title or len(title) < 10:
                    continue

                # 相对路径转绝对路径
                if href.startswith("/"):
                    href = "https://www.theguardian.com" + href

                if href not in visited:
                    visited.add(href)
                    articles.append({"title": title, "url": href})

        except Exception as e:
            print(f"  [LIST] 失败: {e}")

        time.sleep(random.uniform(*REQUEST_DELAY))

    return articles


# ============================================================
# 文章详情解析 — Guardian 文章页
# ============================================================

def fetch_article_detail(session, url, title):
    """
    获取 Guardian 文章详情页的内容。
    提取标题、日期、正文、封面图等信息。
    """
    try:
        resp = with_retry(
            lambda: session.get(url, headers=HEADERS, proxies=PROXIES, timeout=REQUEST_TIMEOUT, verify=False),
            description=f"Guardian文章详情"
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        # 标题：从 h1 标签获取
        h1 = soup.find("h1")
        if h1:
            title = clean(h1.get_text())

        # 日期提取（Guardian 支持多种日期来源）
        date = ""

        # 1. time 标签（优先）
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            date = time_tag.get("datetime")

        # 2. meta 标签（article:published_time）
        if not date:
            meta = soup.find("meta", property="article:published_time")
            if meta:
                date = meta.get("content", "")

        # 3. JSON-LD 结构化数据（最稳定）
        if not date:
            scripts = soup.find_all("script", type="application/ld+json")
            for s in scripts:
                try:
                    data = json.loads(s.string)
                    if isinstance(data, dict) and "datePublished" in data:
                        date = data["datePublished"]
                        break
                except Exception:
                    pass

        # 正文提取：从 article 标签中提取 <p> 标签文本
        content = ""
        article_tag = soup.find("article")
        if article_tag:
            paragraphs = article_tag.select("p")
            content = "\n".join(
                clean(p.get_text()) for p in paragraphs if len(clean(p.get_text())) > 20
            )

        # 正文太短则跳过
        if len(content) < 100:
            print("    [SKIP] 正文太短")
            return None

        # 封面图：从 Open Graph 标签获取
        cover_url = ""
        og_img = soup.find("meta", property="og:image")
        if og_img:
            cover_url = og_img.get("content", "")

        cover_path = ""
        if cover_url:
            cover_path = download_image(cover_url, url)

        return {
            "title": title,
            "url": url,
            "date": date,
            "keywords": extract_keywords(title + " " + content),
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
    爬虫主流程：列表页 → 详情页 → 关键词过滤 → 入库。
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
        print(f"  [{i+1}] {art['title'][:60]}")

        detail = fetch_article_detail(session, art["url"], art["title"])
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
