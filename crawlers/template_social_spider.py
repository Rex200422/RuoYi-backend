"""
社媒爬虫模板（帖子+评论）
=====================================

基于 RuoYi 舆情系统爬虫开发范式。

使用方法：
  1. cp template_social_spider.py your_site_spider.py
  2. 修改 # 配置区 的站点信息
  3. 实现 search_posts() 中的搜索逻辑
  4. 运行测试: python your_site_spider.py --max 3

Windows 开发环境：
  1. 确保 crawler_config.py 中 DB 配置正确
  2. pip install pymysql requests
  3. python your_site_spider.py --max 3

爬虫流程：
  search_posts() → 搜索帖子
    → save_posts() → 下载图片、保存帖子和评论到数据库
"""
import os, sys, re, time, random, argparse, hashlib, uuid  # 基础库：文件操作、正则、时间、随机、参数解析、哈希、UUID
import requests  # HTTP请求库

# ============================================================
# 导入统一配置和工具模块
# ============================================================
from crawler_config import DB, IMAGE_DIR, MAX_PER_KEYWORD, REQUEST_DELAY, ALL_KEYWORDS, extract_keywords
from proxy_config import PROXIES
from common_db import (
    get_db, save_social_post, save_social_comment, save_social_post_image,
    update_crawl_log, update_crawl_log_error,
    update_config_last_crawl, update_crawl_log_start,
)
from retry_utils import with_retry


# ===== 配置区：需要根据目标站点修改 =====
# ============================================================
# 站点配置 — 创建新爬虫时只需修改这里
# ============================================================
SITE_NAME = "YourSite"                       # 站点名（写入 social_post.site_name 字段）


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


# ===== 已写好，通常不需要修改 =====
# ============================================================
# 图片下载函数
# ============================================================

def download_image(url, post_id, idx=0):
    """
    下载图片到本地目录。

    根据 post_id 生成唯一的本地文件名（MD5哈希前16位）。
    如果文件已存在则跳过下载。

    参数:
        url (str): 图片的远程URL
        post_id (str): 帖子ID（用于生成唯一文件名）
        idx (int): 同一帖子内多张图片的序号，从0开始

    返回值:
        str: 相对于 uploadPath 的本地文件路径
             下载失败返回空字符串
    """
    if not url:
        return ""
    post_hash = hashlib.md5(post_id.encode()).hexdigest()[:16]  # MD5哈希前16位作为文件名前缀
    filename = f"{post_hash}_{idx}.jpg"  # 文件名格式：哈希_序号.jpg
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


# ===== 需要开发：实现数据提取逻辑 =====
# ============================================================
# 搜索帖子 — 【在此实现】
# ============================================================

def search_posts(keyword, max_count):
    """
    根据关键词搜索帖子并返回结果。

    这是需要根据目标站点实现的核心函数。
    需要搜索网站、提取帖子列表，并获取评论。

    参数:
        keyword (str): 搜索关键词
        max_count (int): 最多返回的帖子数

    返回值:
        list[dict]: 帖子列表，每个元素包含以下字段:
            - "post_id" (str): 帖子唯一ID（必填，用于去重）
            - "title" (str): 标题/摘要（建议前200字）
            - "author" (str): 作者
            - "content" (str): 完整内容
            - "publish_time" (str): 发布时间，格式 "YYYY-MM-DD HH:MM:SS"
            - "like_count" (int): 点赞数
            - "comment_count" (int): 评论数
            - "original_url" (str): 帖子原始链接
            - "image_urls" (list[str]): 图片URL列表
            - "comments" (list[dict]): 评论列表，每个元素包含:
                - "comment_id" (str): 评论唯一ID
                - "commenter" (str): 评论者
                - "comment_content" (str): 评论内容
                - "like_count" (int): 评论点赞数
                - "comment_time" (str): 评论时间
    """
    posts = []  # 存储搜索到的帖子列表
    # ===== 在此实现搜索逻辑 =====
    return posts


# ===== 已写好，通常不需要修改 =====
# ============================================================
# 入库逻辑
# ============================================================

def save_posts(posts, keyword):
    """
    将搜索到的帖子保存到数据库。

    处理流程：
      1. 下载帖子图片到本地
      2. 将帖子信息保存到 social_post 表
      3. 将评论保存到 social_comment 表
      4. 将图片记录保存到 social_post_image 表

    参数:
        posts (list[dict]): search_posts() 返回的帖子列表
        keyword (str): 搜索时使用的关键词

    返回值:
        tuple: (items_found, items_new, items_updated)
            - items_found: 处理的帖子总数
            - items_new: 新增的帖子数
            - items_updated: 更新的帖子数
    """
    items_found = items_new = items_updated = 0  # 统计计数器

    for post in posts:
        items_found += 1  # 增加发现计数
        post_id = post["post_id"]  # 帖子唯一ID

        conn = get_db()  # 获取数据库连接
        cur = conn.cursor()  # 创建游标
        try:
            # 下载图片
            image_path = ""  # 主图路径
            if post.get("image_urls"):  # 如果有图片
                image_path = download_image(post["image_urls"][0], post_id, 0)  # 下载主图
                for idx, img_url in enumerate(post["image_urls"]):  # 下载所有图片
                    local = download_image(img_url, post_id, idx)  # 下载图片到本地
                    if local:  # 下载成功则保存记录
                        cur.execute(  # 插入图片记录
                            "INSERT INTO social_post_image (post_id, image_url, local_path, idx) "
                            "VALUES (%s, %s, %s, %s)", (post_id, img_url, local, idx))

            # 保存帖子
            post_data = {  # 构建帖子数据
                "uuid": str(uuid.uuid4()), "site_name": SITE_NAME,
                "post_id": post_id, "trigger_keyword": keyword,
                "title": post.get("title", ""),
                "author": post.get("author", ""),
                "publish_time": post.get("publish_time", ""),
                "like_count": post.get("like_count", 0),
                "comment_count": post.get("comment_count", 0),
                "content": post.get("content", ""),
                "original_url": post.get("original_url", ""),
                "image_url": image_path,
            }
            is_new, is_updated = save_social_post(cur, post_data)  # 保存帖子
            if is_new:  # 新增帖子
                items_new += 1
                print(f"    [NEW] {post.get('author', '?')}: {post.get('content', '')[:50]}")
            elif is_updated:  # 更新帖子
                items_updated += 1
                print(f"    [UPDATE] {post_id[:30]}")

            # 保存评论
            for c in post.get("comments", []):  # 遍历评论
                save_social_comment(cur, {  # 保存评论
                    "post_id": post_id,
                    "title": post.get("title", ""),
                    "comment_id": c["comment_id"],
                    "commenter": c.get("commenter", ""),
                    "comment_content": c.get("comment_content", ""),
                    "like_count": c.get("like_count", 0),
                    "comment_time": c.get("comment_time", ""),
                })

            conn.commit()  # 提交事务
        finally:
            cur.close()  # 关闭游标
            conn.close()  # 关闭连接
        time.sleep(random.uniform(*REQUEST_DELAY))  # 随机延迟，防止被封IP

    return items_found, items_new, items_updated


# ===== 已写好，通常不需要修改 =====
# ============================================================
# 主流程
# ============================================================

def crawl(keywords, max_per_kw):
    """
    爬虫主流程：按关键词搜索 → 保存帖子。

    流程说明：
      1. 遍历每个关键词
      2. 调用 search_posts() 搜索帖子（多搜一些用于过滤）
      3. 取前 max_per_kw 条帖子
      4. 调用 save_posts() 保存到数据库

    参数:
        keywords (list[str]): 关键词列表
        max_per_kw (int): 每个关键词最多保存的帖子数

    返回值:
        tuple: (total_found, total_new, total_updated)
            - total_found: 所有关键词发现的帖子总数
            - total_new: 新增的帖子数
            - total_updated: 更新的帖子数
    """
    print(f"\n{'='*50}")
    print(f"  {SITE_NAME} Spider  keywords={keywords}  max={max_per_kw}")
    print(f"{'='*50}")

    total_found = total_new = total_updated = 0  # 统计计数器

    for kw in keywords:  # 遍历每个关键词
        print(f"\n--- 搜索: {kw} ---")
        posts = search_posts(kw, max_per_kw * 3)[:max_per_kw]  # 多搜一些用于过滤，取前max_per_kw条
        found, new, updated = save_posts(posts, kw)  # 保存帖子
        total_found += found  # 累加统计
        total_new += new
        total_updated += updated
        print(f"  {kw}: found={found} new={new} updated={updated}")

    print(f"\n  Done. found={total_found} new={total_new} updated={total_updated}\n")
    return total_found, total_new, total_updated


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
        --max:       每个关键词最多爬取的帖子数（覆盖默认值 MAX_PER_KEYWORD）
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
      2. 确定关键词列表（--keyword 指定单个关键词，否则使用 ALL_KEYWORDS）
      3. 更新爬取日志的开始时间
      4. 执行爬虫主流程
      5. 成功时更新日志和配置
      6. 失败时记录错误信息
    """
    args = parse_args()  # 解析命令行参数
    keywords = [args.keyword] if args.keyword else ALL_KEYWORDS  # 确定关键词列表
    max_per_kw = args.max or MAX_PER_KEYWORD  # 每个关键词最多爬取数

    update_crawl_log_start(args.log_id)  # 记录开始时间
    try:
        found, new, updated = crawl(keywords, max_per_kw)  # 执行爬虫
        update_crawl_log(args.log_id, found, new, updated)  # 更新日志
        update_config_last_crawl(args.config_id)  # 更新配置
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))  # 记录错误
        raise


if __name__ == "__main__":
    main()
