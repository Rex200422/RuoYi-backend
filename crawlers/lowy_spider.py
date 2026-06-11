"""
Lowy Institute 新闻爬虫
=====================================

基于 RuoYi 舆情系统爬虫开发范式。
从 Lowy Institute 中国话题页面抓取文章。

使用方法：
  python lowy_spider.py --max 3

爬虫流程：
  fetch_article_list() → 收集文章链接
    → fetch_article_detail() → 获取每篇文章详情
      → 关键词过滤
        → save_news_article() → 保存到数据库
"""
import os, sys, re, time, random, argparse, hashlib, json  # 基础库
from urllib.parse import urljoin, urlparse  # URL处理库

import requests  # HTTP请求库
from bs4 import BeautifulSoup  # HTML解析库

# ============================================================
# 导入统一配置和工具模块
# ============================================================
import crawler_config
from crawler_config import DB, IMAGE_DIR, MAX_ARTICLES, MAX_PAGES, REQUEST_TIMEOUT, REQUEST_DELAY, USER_AGENT, ALL_KEYWORDS, extract_keywords, contains_main_keyword
from proxy_config import PROXIES

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}
from content_utils import clean_content_html, remove_boilerplate_text
from common_db import (
    get_db, save_news_article,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)
from retry_utils import with_retry


# ============================================================
# 站点配置 — Lowy Institute 中国话题
# ============================================================
SITE_NAME = "Lowy"                                              # 站点名（写入 news_article.source 字段）
BASE_URL = "https://www.lowyinstitute.org"                       # Lowy 基础域名
TOPIC_URL = "https://www.lowyinstitute.org/topics/china"         # 中国话题列表页URL
IMAGE_DIR = IMAGE_DIR  # 图片目录（从 crawler_config 自动切换，一般无需修改）


# ============================================================
# 工具函数
# ============================================================

def clean(text):
    """
    清理文本中的多余空白字符。
    """
    return re.sub(r"\s+", " ", text).strip() if text else ""


def clean_text(text):
    """
    清理文本，保留换行结构。
    """
    if not text:
        return ""
    lines = []
    for line in text.replace("\xa0", " ").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


# ============================================================
# URL 工具函数（Lowy 专用）
# ============================================================

def normalize_url(href):
    """
    标准化 Lowy URL，移除查询参数和锚点，确保格式统一。
    """
    if not href:
        return ""
    href = href.strip()
    url = urljoin(BASE_URL, href)
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def is_lowy_article_url(url):
    """
    判断 URL 是否为 Lowy 的文章页面。
    Lowy 文章路径以 /publications/ 或 /the-interpreter/ 开头。
    """
    if not url.startswith(BASE_URL):
        return False
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # 排除列表页本身和归档页
    if path in {"/publications", "/the-interpreter"}:
        return False
    if "/archive" in path:
        return False

    return path.startswith("/publications/") or path.startswith("/the-interpreter/")


# ============================================================
# JSON-LD 结构化数据提取（Lowy 专用）
# ============================================================

def load_json_ld(soup):
    """
    从页面中提取所有 JSON-LD 结构化数据。
    """
    items = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            items.extend(data)
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                items.extend(data["@graph"])
            else:
                items.append(data)
    return items


def pick_article_schema(items):
    """
    从 JSON-LD 数据中选择文章类型的 schema。
    """
    article_types = {"Article", "NewsArticle", "ScholarlyArticle", "Report", "BlogPosting"}
    for item in items:
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            types = set(item_type)
        else:
            types = {item_type}
        if article_types & types:
            return item
    return items[0] if items else {}


def extract_author(author_value, soup):
    """
    从 JSON-LD 的 author 字段提取作者名。
    """
    names = []
    if isinstance(author_value, dict):
        name = clean_text(author_value.get("name", ""))
        if name:
            names.append(name)
    elif isinstance(author_value, list):
        for author in author_value:
            if isinstance(author, dict):
                name = clean_text(author.get("name", ""))
            else:
                name = clean_text(str(author))
            if name:
                names.append(name)
    elif author_value:
        names.append(clean_text(str(author_value)))

    # 回退到 meta 标签
    if not names:
        meta = soup.find("meta", attrs={"name": "author"})
        if meta:
            name = clean_text(meta.get("content", ""))
            if name:
                names.append(name)

    return ", ".join(dict.fromkeys(names))


def extract_metadata(soup):
    """
    从 Lowy 文章页面提取元数据（标题、日期、作者）。
    优先使用 JSON-LD 结构化数据。
    """
    schema = pick_article_schema(load_json_ld(soup))
    title = clean_text(schema.get("headline", ""))
    date = clean_text(schema.get("datePublished", ""))
    author = extract_author(schema.get("author"), soup)

    # 回退到 h1 标签
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = clean_text(h1.get_text("\n", strip=True))

    # 回退到 title 标签
    if not title and soup.title:
        title = clean_text(soup.title.get_text()).replace(" | Lowy Institute", "")

    # 回退到 meta 标签
    if not date:
        meta = soup.find("meta", property="article:published_time")
        if meta:
            date = clean_text(meta.get("content", ""))

    return title, date, author


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
# 列表页解析 — Lowy 中国话题
# ============================================================

def fetch_article_list(session, max_pages):
    """
    从 Lowy 中国话题页面收集文章链接。
    Lowy 的话题页面包含指向 /publications/ 和 /the-interpreter/ 的链接。
    """
    articles = []  # 存储收集到的文章列表
    visited = set()  # 已访问的URL集合，用于去重

    for page_num in range(1, max_pages + 1):
        # Lowy 话题页分页格式: /topics/china?page=2
        url = TOPIC_URL if page_num == 1 else f"{TOPIC_URL}?page={page_num}"
        print(f"  [LIST] Page {page_num}: {url}")

        try:
            resp = with_retry(
                lambda: session.get(url, headers=HEADERS, proxies=PROXIES, timeout=REQUEST_TIMEOUT),
                description=f"Lowy话题页第{page_num}页"
            )
            soup = BeautifulSoup(resp.text, "html.parser")

            # Lowy 话题页中的文章链接
            for a_tag in soup.find_all("a", href=True):
                href = normalize_url(a_tag.get("href"))

                if not is_lowy_article_url(href):
                    continue

                if href in visited:
                    continue

                visited.add(href)
                list_title = clean_text(a_tag.get_text("\n", strip=True))
                articles.append({"title": list_title, "url": href})

        except Exception as e:
            print(f"  [LIST] 失败: {e}")

        time.sleep(random.uniform(*REQUEST_DELAY))

    return articles


# ============================================================
# 文章详情解析 — Lowy 文章页
# ============================================================

def fetch_article_detail(session, url, title):
    """
    获取 Lowy 文章详情页的内容。
    使用 JSON-LD 结构化数据提取元数据，
    使用文本密度最大的 div 提取正文。
    """
    try:
        resp = with_retry(
            lambda: session.get(url, headers=HEADERS, proxies=PROXIES, timeout=REQUEST_TIMEOUT),
            description=f"Lowy文章详情"
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        # 元数据提取：标题、日期、作者
        meta_title, date, author = extract_metadata(soup)
        if meta_title:
            title = meta_title

        # 正文提取：选择文本最长的 div（Lowy 文章正文在 prose 类 div 中）
        content = ""
        for div in soup.find_all("div"):
            text = clean_text(div.get_text("\n", strip=True))
            if len(text) > 3000:
                if len(text) > len(content):
                    content = text

        # 截断模板文字（参考标记之后的内容）
        if content:
            end_markers = ["References", "About the author", "About the authors", "Topics"]
            end_pos = len(content)
            for marker in end_markers:
                pos = content.find(marker)
                if pos != -1:
                    end_pos = min(end_pos, pos)
            content = content[:end_pos]
            content = re.sub(r"\s+", " ", content).strip()
            content = content[:20000]

        # 正文太短则跳过
        if not content or len(content) < 100:
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
