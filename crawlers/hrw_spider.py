"""
HRW (Human Rights Watch) 新闻爬虫 - Playwright 版
=====================================================

功能概述：
    使用 Playwright 无头浏览器抓取 Human Rights Watch 网站的新闻文章。
    HRW 网站大量使用 JavaScript 渲染，因此需要 Playwright 而非 requests。

技术方案：
    1. 启动 Chromium 无头浏览器（带代理）
    2. 访问列表页，解析 <article> 标签获取文章链接
    3. 逐个访问文章详情页，提取标题、日期、正文、封面图
    4. 使用关键词过滤后保存到数据库

关键词过滤：
    主关键词: china, taiwan（必须匹配至少一个）
    子关键词: trade, technology, military, sanctions 等（用于标记分类）

使用方式:
    python hrw_spider.py --max 3
    python hrw_spider.py --max 5 --log-id 123
"""
import os, sys, re, time, random, argparse
from playwright.sync_api import sync_playwright
from content_utils import extract_content_playwright, remove_boilerplate_text
from common_db import get_db, save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
from crawler_config import DB, IMAGE_DIR
import hashlib, requests as req_lib
from proxy_config import PROXIES, get_playwright_proxy


# ============================================================
# 图片下载
# ============================================================

def download_image(url, article_id, idx=0):
    """
    下载图片到本地，返回本地文件名。

    使用 MD5 哈希（前16位）作为文件名前缀，避免冲突。
    如果文件已存在则跳过下载。
    下载时先尝试代理，失败后回退到直连。

    参数:
        url (str): 图片远程URL
        article_id (str): 文章标识符（用于生成唯一文件名）
        idx (int): 同一文章内多张图片的序号

    返回值:
        str: 本地文件名（不含目录），下载失败返回空字符串
    """
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


# ============================================================
# 配置常量
# ============================================================

SITE_NAME = "HRW"                           # 站点名
BASE_URL = "https://www.hrw.org"            # HRW 网站基础URL
DEFAULT_MAX_PAGES = 2                       # 默认最大列表页数
DEFAULT_MAX_ARTICLES = 2                    # 默认最大文章数

# 主关键词（必须匹配至少一个才保存）
MAIN_KEYWORDS = ["china", "taiwan"]
# 子关键词（用于标记文章分类，不要求必须匹配）
SUB_KEYWORDS = ["trade", "technology", "military", "sanctions", "indo-pacific", "south china sea",
                "semiconductor", "cyber", "beijing", "human rights", "xinjiang", "hong kong",
                "uyghur", "tibet", "ccp"]


# ============================================================
# 基础工具函数
# ============================================================

def clean(text):
    """清理文本中的多余空白"""
    return re.sub(r"\s+", " ", text).strip() if text else ""


# ============================================================
# 关键词工具
# ============================================================

def extract_keywords(text):
    """
    从文本中提取所有匹配的关键词（主关键词 + 子关键词）。

    参数:
        text (str): 待匹配的文本

    返回值:
        str: 逗号分隔的关键词
    """
    t = text.lower()
    return ",".join(sorted(set(k for k in MAIN_KEYWORDS + SUB_KEYWORDS if re.search(rf"\b{re.escape(k)}\b", t))))

def contains_keywords(text):
    """
    检查文本是否包含主关键词（china 或 taiwan）。

    参数:
        text (str): 待检查的文本

    返回值:
        bool: 包含主关键词返回 True
    """
    t = text.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in MAIN_KEYWORDS)


# ============================================================
# Playwright 页面提取辅助函数
# ============================================================

def extract_cover_image(page):
    """
    从页面提取封面图URL。

    依次尝试 og:image 和 twitter:image 两个 meta 标签。

    参数:
        page: Playwright Page 对象

    返回值:
        str: 封面图URL，找不到返回空字符串
    """
    meta = page.locator("meta[property='og:image']")
    if meta.count() > 0:
        return meta.first.get_attribute("content") or ""
    meta2 = page.locator("meta[name='twitter:image']")
    if meta2.count() > 0:
        return meta2.first.get_attribute("content") or ""
    return ""

def get_page_url(page_num):
    """
    生成列表页URL。

    HRW 网站使用 country 参数筛选中国相关新闻（9545是中国的国家代码）。

    参数:
        page_num (int): 页码，0 表示第一页

    返回值:
        str: 完整的列表页URL
    """
    if page_num == 0:
        return "https://www.hrw.org/news?country%5B0%5D=9545"
    return f"https://www.hrw.org/news?country%5B0%5D=9545&page={page_num}"

def extract_date(page):
    """
    从页面提取文章发布日期。

    依次尝试 article:published_time meta 标签和 <time> 标签。

    参数:
        page: Playwright Page 对象

    返回值:
        str: 发布日期，找不到返回空字符串
    """
    meta = page.locator("meta[property='article:published_time']")
    if meta.count() > 0: return meta.first.get_attribute("content") or ""
    t = page.locator("time")
    if t.count() > 0:
        dt = t.first.get_attribute("datetime")
        if dt: return dt
        return clean(t.first.inner_text())
    return ""

def is_valid_article(url):
    """
    判断URL是否是有效的新闻文章链接。

    过滤掉报告、图片集、标签页等非新闻页面。

    参数:
        url (str): 文章URL

    返回值:
        bool: 有效返回 True
    """
    if not url.startswith(BASE_URL): return False
    bad = ["/report/", "/video-photos/", "/tag/", "/about/", "/contact"]
    return not any(x in url for x in bad)


# ============================================================
# 爬虫主流程
# ============================================================

def crawl(max_pages, max_articles):
    """
    爬虫主流程：使用 Playwright 浏览器抓取 HRW 新闻。

    流程说明：
      1. 启动 Chromium 无头浏览器（配置代理）
      2. 遍历列表页，从 <article> 标签中提取文章链接
      3. 去重后，逐个访问文章详情页
      4. 提取标题（h1）、日期（meta/time）、正文（<article>标签）、封面图（og:image）
      5. 通过关键词过滤（必须包含 china 或 taiwan）
      6. 保存到数据库

    技术细节：
      - 使用 Playwright 而非 requests，因为 HRW 网站有 JS 渲染
      - 每次页面跳转后等待 domcontentloaded 事件
      - 列表页使用 locator 定位 article 标签，逐个提取链接
      - 详情页的正文通过 clean() 清理后存储

    参数:
        max_pages (int): 最多遍历的列表页数
        max_articles (int): 最多保存的文章数

    返回值:
        tuple: (items_found, items_new, items_updated)
    """
    items_found = 0
    items_new = 0
    items_updated = 0
    page_failures = 0
    visited_urls = set()
    conn = get_db()
    cur = conn.cursor()
    try:
        with sync_playwright() as p:
            # 启动浏览器：headless 模式，配置代理，禁用不必要功能
            browser = p.chromium.launch(headless=True, proxy=get_playwright_proxy(), args=["--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--no-sandbox"])
            page = browser.new_page()
            news_list = []

            # 第一阶段：收集文章链接
            for page_num in range(1, max_pages + 1):
                url = get_page_url(page_num)
                print(f"\n访问：{url}")
                try:
                    page.goto(url, timeout=60000)
                    page.wait_for_load_state("domcontentloaded")
                except Exception as e:
                    print(f"页面失败：{e}")
                    page_failures += 1
                    continue
                articles = page.locator("article")
                print(f"文章块：{articles.count()}")
                for i in range(articles.count()):
                    try:
                        item = articles.nth(i)
                        links = item.locator("a")
                        for j in range(links.count()):
                            a = links.nth(j)
                            href = a.get_attribute("href")
                            title = clean(a.inner_text())
                            if href and len(title) > 15:
                                if href.startswith("/"): href = BASE_URL + href
                                if is_valid_article(href) and href not in visited_urls:
                                    visited_urls.add(href)
                                    news_list.append({"title": title, "url": href})
                                    print(f"收录：{title}")
                                break
                    except:
                        pass

            # 检查列表页是否全部失败
            if page_failures > 0 and len(news_list) == 0:
                raise Exception(f"所有列表页访问失败({page_failures}次)，代理可能不可用")
            print(f"\n总文章数：{len(news_list)}")

            # 第二阶段：逐个访问文章详情页
            for news in news_list:
                if (items_new + items_updated) >= max_articles:
                    break
                try:
                    print(f"\n采集：{news['title']}")
                    page.goto(news["url"], timeout=60000)
                    page.wait_for_load_state("domcontentloaded")

                    # 提取标题（h1 标签）
                    h1 = page.locator("h1")
                    if h1.count() > 0: news["title"] = clean(h1.first.inner_text())

                    # 提取日期
                    date = extract_date(page)

                    # 提取封面图
                    cover_url = extract_cover_image(page)
                    cover_image = download_image(cover_url, news["url"]) if cover_url else ""

                    # 提取正文
                    article_el = page.locator("article")
                    if article_el.count() == 0:
                        print("无正文")
                        continue
                    content = clean(article_el.first.inner_text())
                    if len(content) < 300:
                        print("正文太短")
                        continue

                    items_found += 1

                    # 关键词过滤
                    if not contains_keywords(content):
                        print(f"跳过(无关键词) content_len={len(content)}")
                        continue

                    keywords = extract_keywords(content)
                    article_data = {"title": news["title"], "url": news["url"], "date": date,
                                    "content": content[:5000], "keywords": keywords,
                                    "cover_image": "sentiment/images/" + cover_image if cover_image else "", "source": SITE_NAME}

                    # 保存到数据库
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
        cur.close()
        conn.close()
    print(f"\n=== Done. New: {items_new}, Updated: {items_updated}, Total found: {items_found} ===")
    return items_found, items_new, items_updated


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。

    参数说明:
        --config-id: 爬取配置ID（由调度系统传入）
        --keyword:   预留参数（当前 HRW 爬虫使用固定关键词）
        --max:       最多爬取的文章数
        --log-id:    爬取日志ID（用于更新日志状态）
    """
    parser = argparse.ArgumentParser(description="HRW Spider")
    parser.add_argument("--config-id", type=int, default=None)
    parser.add_argument("--keyword", type=str, default=None)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--log-id", type=int, default=None)
    return parser.parse_args()

def main():
    """
    主函数：解析参数 → 执行爬虫 → 更新日志。

    负责：
      1. 解析命令行参数
      2. 如果传入了 --keyword，覆盖默认关键词列表
      3. 记录爬取开始时间
      4. 调用 crawl() 执行爬虫
      5. 成功时更新日志和配置
      6. 失败时记录错误信息
    """
    global MAIN_KEYWORDS
    args = parse_args()
    max_articles = args.max if args.max is not None else DEFAULT_MAX_ARTICLES
    # 如果传入了 --keyword（如 "china,taiwan"），覆盖默认关键词
    if args.keyword:
        MAIN_KEYWORDS = [kw.strip() for kw in args.keyword.split(",") if kw.strip()]
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
