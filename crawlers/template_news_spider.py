"""
[RSS/HTML] 新闻爬虫模板
=====================================

基于 RuoYi 舆情系统爬虫开发范式。

使用方法：
  1. cp template_news_spider.py your_site_spider.py
  2. 修改 # 配置区 的站点信息
  3. 实现 fetch_article_list() 和 fetch_article_detail() 中的数据提取逻辑
  4. 运行测试: python your_site_spider.py --max 3

Windows 开发环境：
  1. 确保 crawler_config.py 中 DB 配置正确
  2. pip install pymysql requests beautifulsoup4 lxml
  3. python your_site_spider.py --max 3

爬虫流程：
  fetch_article_list() → 收集文章链接
    → fetch_article_detail() → 获取每篇文章详情
      → 关键词过滤
        → save_news_article() → 保存到数据库
"""
import os, sys, re, time, random, argparse, hashlib  # 基础库：文件操作、正则、时间、随机、参数解析、哈希
import requests  # HTTP请求库
from bs4 import BeautifulSoup  # HTML解析库

# ============================================================
# 导入统一配置和工具模块
# ============================================================
from crawler_config import DB, IMAGE_DIR, MAX_ARTICLES, MAX_PAGES, REQUEST_TIMEOUT, REQUEST_DELAY, USER_AGENT
from proxy_config import PROXIES
HEADERS = {"User-Agent": USER_AGENT}
from content_utils import clean_content_html, remove_boilerplate_text
from common_db import (
    get_db, save_news_article,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)
from retry_utils import with_retry


# ===== 配置区：需要根据目标站点修改 =====
# ============================================================
# 站点配置 — 创建新爬虫时只需修改这里
# ============================================================
SITE_NAME = "YourSite"                       # 站点名（写入 news_article.source 字段）
BASE_URL = "https://example.com/news"        # 列表页URL（爬虫从这里开始）
KEYWORDS = ["china", "taiwan"]               # 关键词列表，用于过滤文章
IMAGE_DIR = IMAGE_DIR                        # 图片目录（从 crawler_config 自动切换，一般无需修改）


# ============================================================
# 工具函数
# ============================================================

def clean(text):
    """
    清理文本中的多余空白字符。

    参数:
        text (str): 待清理的文本

    返回值:
        str: 替换所有连续空白为单个空格后的文本
    """
    return re.sub(r"\s+", " ", text).strip() if text else ""  # \s+ 匹配一个或多个空白字符


def extract_keywords(text):
    """
    从文本中提取匹配的关键词。

    扫描文本，找出所有在 KEYWORDS 列表中出现的关键词，
    返回逗号分隔的去重排序结果。

    参数:
        text (str): 待匹配的文本（会转为小写匹配）

    返回值:
        str: 逗号分隔的关键词，如 "china,taiwan"
    """
    t = text.lower()  # 转小写以便统一匹配
    return ",".join(sorted(set(
        k for k in KEYWORDS
        if re.search(rf"\b{re.escape(k)}\b", t)  # \b 匹配单词边界，re.escape 避免关键词含特殊字符
    )))


def contains_main_keyword(text):
    """
    检查文本是否包含主关键词。

    参数:
        text (str): 待检查的文本（会转为小写匹配）

    返回值:
        bool: 如果文本中包含任意一个 KEYWORDS 中的关键词则返回 True
    """
    t = text.lower()  # 转小写以便统一匹配
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in KEYWORDS)  # \b 匹配单词边界


# ===== 已写好，通常不需要修改 =====
# ============================================================
# 图片下载函数
# ============================================================

def download_image(url, identifier, idx=0):
    """
    下载图片到本地目录。

    根据 identifier（通常是文章URL）生成唯一的本地文件名（MD5哈希前16位）。
    如果文件已存在则跳过下载。

    参数:
        url (str): 图片的远程URL
        identifier (str): 标识符（用于生成唯一文件名，通常是文章URL）
        idx (int): 同一文章内多张图片的序号，从0开始

    返回值:
        str: 相对于 uploadPath 的本地文件路径，如 "sentiment/images/abc123_0.jpg"
             下载失败返回空字符串
    """
    if not url:
        return ""
    id_hash = hashlib.md5(identifier.encode()).hexdigest()[:16]  # MD5哈希前16位作为文件名前缀
    filename = f"{id_hash}_{idx}.jpg"  # 文件名格式：哈希_序号.jpg
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        # 返回相对于 uploadPath 的路径
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


# ===== 需要开发：实现数据提取逻辑 =====
# ============================================================
# 列表页解析 — 【在此实现】
# ============================================================

def fetch_article_list(session, max_pages):
    """
    从列表页收集文章链接。

    遍历列表页的多个页面，提取每篇文章的标题和URL。
    使用 requests.Session 保持 Cookie。

    参数:
        session: requests.Session 对象（保持连接和Cookie）
        max_pages (int): 最多遍历的列表页数

    返回值:
        list[dict]: 文章列表，每个元素包含:
            - "title" (str): 文章标题
            - "url" (str): 文章详情页URL
    """
    articles = []  # 存储收集到的文章列表
    visited = set()  # 已访问的URL集合，用于去重

    for page_num in range(1, max_pages + 1):
        url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"  # 第一页不需要分页参数
        print(f"  [LIST] Page {page_num}: {url}")
        try:
            resp = session.get(url, headers=HEADERS, proxies=PROXIES, timeout=30)  # 发送请求
            soup = BeautifulSoup(resp.text, "html.parser")  # 解析HTML

            # ===== 根据站点结构调整选择器 =====
            # 以下是常见的文章链接选择器，需要根据目标站点的HTML结构调整
            for a_tag in soup.select("article a[href], .post-title a, h2 a"):  # 常见文章链接选择器
                href = a_tag.get("href", "").strip()  # 获取链接
                title = clean(a_tag.get_text())  # 获取标题并清理空白
                if not href or len(title) < 10:  # 过滤无效链接和过短标题
                    continue
                if href.startswith("/"):  # 相对路径转绝对路径
                    href = BASE_URL.rstrip("/") + href
                if href not in visited:
                    visited.add(href)
                    articles.append({"title": title, "url": href})

        except Exception as e:
            print(f"  [LIST] 失败: {e}")

    return articles


# ===== 需要开发：实现数据提取逻辑 =====
# ============================================================
# 文章详情解析 — 【在此实现】
# ============================================================

def fetch_article_detail(session, url, title):
    """
    获取文章详情页的内容。

    访问文章URL，提取标题、日期、正文、封面图等信息。
    使用 content_utils 中的清洗函数处理正文HTML。

    参数:
        session: requests.Session 对象
        url (str): 文章详情页URL
        title (str): 从列表页获取的标题（备用）

    返回值:
        dict: 文章数据，包含以下字段:
            - "title" (str): 文章标题
            - "url" (str): 文章URL
            - "date" (str): 发布日期
            - "keywords" (str): 逗号分隔的关键词
            - "content" (str): 清洗后的HTML正文
            - "cover_image" (str): 封面图本地路径
            - "source" (str): 来源站点名
        如果正文太短或获取失败，返回 None
    """
    try:
        resp = session.get(url, headers=HEADERS, proxies=PROXIES, timeout=30, verify=False)  # 发送请求，verify=False禁用SSL验证
        soup = BeautifulSoup(resp.text, "html.parser")  # 解析HTML

        h1 = soup.find("h1")  # 尝试从h1标签获取标题
        if h1:
            title = clean(h1.get_text())  # 清理标题空白

        date = ""  # 日期字符串
        time_tag = soup.find("time")  # 尝试从time标签获取日期
        if time_tag:
            date = time_tag.get("datetime") or clean(time_tag.get_text())  # 优先使用datetime属性

        content = ""  # 文章正文
        article_tag = soup.find("article") or soup.find("div", class_="entry-content")  # 定位正文容器
        if article_tag:
            content = clean_content_html(str(article_tag))  # 清洗HTML内容
        if not content or len(content) < 100:  # 正文太短时尝试从meta标签获取摘要
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                content = remove_boilerplate_text(meta["content"])  # 清理模板文字
        if not content or len(content) < 100:  # 仍然太短则跳过
            print(f"    [SKIP] 正文太短")
            return None

        cover_url = ""  # 封面图URL
        og_img = soup.find("meta", property="og:image")  # 从Open Graph标签获取封面图
        if og_img and og_img.get("content"):
            cover_url = og_img["content"]
        cover_path = download_image(cover_url, url) if cover_url else ""  # 下载封面图

        return {
            "title": title, "url": url, "date": date,
            "keywords": extract_keywords(title + " " + content),
            "content": content, "cover_image": cover_path, "source": SITE_NAME,
        }
    except Exception as e:
        print(f"    [DETAIL] 失败: {e}")
        return None


# ===== 已写好，通常不需要修改 =====
# ============================================================
# 主流程
# ============================================================

def crawl(max_articles, max_pages, keyword=None):
    """
    爬虫主流程：列表页 → 详情页 → 关键词过滤 → 入库。

    流程说明：
      1. 调用 fetch_article_list() 收集文章链接
      2. 逐个访问文章详情页
      3. 过滤不含关键词的文章
      4. 调用 save_news_article() 保存到数据库
      5. 记录统计信息

    参数:
        max_articles (int): 最多保存的文章数
        max_pages (int): 最多遍历的列表页数
        keyword (str or None): 指定关键词（逗号分隔），覆盖模板默认的 KEYWORDS

    返回值:
        tuple: (items_found, items_new, items_updated)
            - items_found: 发现并处理的文章总数
            - items_new: 新增的文章数
            - items_updated: 更新的文章数
    """
    print(f"\n{'='*50}")
    print(f"  {SITE_NAME} Spider  max={max_articles}  pages={max_pages}")
    print(f"{'='*50}")

    session = requests.Session()
    items_found = items_new = items_updated = 0  # 统计计数器

    article_list = fetch_article_list(session, max_pages)
    print(f"\n  收集到 {len(article_list)} 篇文章\n")

    for i, art in enumerate(article_list):
        if (items_new + items_updated) >= max_articles:  # 达到最大文章数则停止
            break
        print(f"  [{i+1}] {art['title'][:60]}")  # 打印前60个字符的标题

        detail = fetch_article_detail(session, art["url"], art["title"])
        if not detail:
            continue
        if not contains_main_keyword(detail["title"] + " " + detail["content"]):  # 关键词过滤
            print(f"    [SKIP] 无关键词")
            continue

        items_found += 1  # 增加发现计数
        conn = get_db()  # 获取数据库连接
        cur = conn.cursor()  # 创建游标
        try:
            is_new, is_updated = save_news_article(cur, detail)  # 保存文章
            conn.commit()  # 提交事务
            if is_new:
                items_new += 1  # 新增文章
                print(f"    [NEW] +1")
            elif is_updated:
                items_updated += 1  # 更新文章
                print(f"    [UPDATE] +1")
            else:
                print(f"    [NOCHANGE]")  # 内容无变化
        finally:
            cur.close()
            conn.close()
        time.sleep(random.uniform(*REQUEST_DELAY))  # 随机延迟，防止被封IP

    print(f"\n  Done. found={items_found} new={items_new} updated={items_updated}\n")
    return items_found, items_new, items_updated


# ===== 已写好，通常不需要修改 =====
# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。

    参数说明:
        --config-id: 爬取配置ID（由调度系统传入）
        --keyword:   指定关键词（覆盖默认关键词列表）
        --max:       最多爬取的文章数（覆盖默认值 MAX_ARTICLES）
        --log-id:    爬取日志ID（由调度系统传入，用于更新日志）

    返回值:
        argparse.Namespace: 解析后的参数对象
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

    这是爬虫的入口点，负责：
      1. 解析命令行参数
      2. 如果传入了 --keyword，覆盖模板默认关键词列表
      3. 更新爬取日志的开始时间
      4. 执行爬虫主流程
      5. 成功时更新日志和配置
      6. 失败时记录错误信息
    """
    global KEYWORDS
    global KEYWORDS  # 声明使用全局变量
    args = parse_args()  # 解析命令行参数
    if args.keyword:  # 如果指定了关键词，覆盖默认值
        KEYWORDS = [kw.strip() for kw in args.keyword.split(",") if kw.strip()]
    update_crawl_log_start(args.log_id)  # 记录开始时间
    try:
        found, new, updated = crawl(  # 执行爬虫
            args.max or MAX_ARTICLES,  # 最大文章数
            MAX_PAGES,  # 最大页数
            args.keyword,  # 关键词
        )
        update_crawl_log(args.log_id, found, new, updated)  # 更新日志
        update_config_last_crawl(args.config_id)  # 更新配置
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))  # 记录错误
        raise


if __name__ == "__main__":
    main()
