"""
Bluesky 社交媒体爬虫
=====================

功能概述：
    使用 atproto（Bluesky API 客户端）抓取 Bluesky 平台的帖子和评论。
    按关键词搜索帖子，下载图片，保存帖子和评论到数据库。

技术方案（atproto 方式）：
    1. 使用 atproto.Client 登录 Bluesky 账号
    2. 调用 app.bsky.feed.search_posts 搜索关键词
    3. 对搜索结果按是否有图片排序（优先处理有图帖子）
    4. 对每个帖子调用 get_post_thread 获取完整线程（帖子+评论）
    5. 递归解析线程树，保存主帖和所有评论
    6. 新帖子完整保存，已有帖子只更新互动数据（点赞/评论数）

与 Playwright 方式的区别：
    - atproto 直接调用 API，不需要浏览器，速度更快
    - 但只能获取 API 暴露的数据，页面展示的内容可能更多
    - 需要 Bluesky 账号凭证

关键词: china, taiwan
每个关键词最多爬取 DEFAULT_MAX_PER_KW 条帖子及其评论 + 图片
"""
import os
import sys
import uuid
import hashlib
import argparse
from datetime import datetime
from proxy_config import PROXIES
os.environ["HTTP_PROXY"] = PROXIES["http"]
os.environ["HTTPS_PROXY"] = PROXIES["https"]

from atproto import Client
import pymysql
import time
import requests
from common_db import save_social_post, save_social_comment, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start


# ============================================================
# 配置常量
# ============================================================

DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
# Bluesky 登录凭证（优先从环境变量读取）
BSKY_USERNAME = os.environ.get("BSKY_USERNAME", "zao-17.bsky.social")
BSKY_PASSWORD = os.environ.get("BSKY_PASSWORD", "3ORI6-VJAFI")
# 搜索关键词列表
ALL_KEYWORDS = ["china", "taiwan"]
# 关键词的显示名称（用于数据库记录）
KEYWORD_DISPLAY = {"china": "China", "taiwan": "Taiwan", "trade": "Trade", "technology": "Technology",
                    "military": "Military", "sanctions": "Sanctions", "tariff": "Tariff",
                    "investment": "Investment", "financial": "Financial"}
DEFAULT_MAX_PER_KW = 2                     # 每个关键词最多保存的帖子数
DEPTH = 3                                   # 评论线程递归深度（3层 = 主帖 + 2层回复）


# 图片存储目录（Linux 生产环境路径）
IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"


# ============================================================
# 数据库工具
# ============================================================

def get_db():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


# ============================================================
# 评论保存
# ============================================================

def save_comment(cur, c):
    """
    保存评论到 social_comment 表。

    使用 INSERT ... ON DUPLICATE KEY UPDATE 去重：
      - 新评论：直接插入
      - 已有评论：更新 like_count 和 comment_content

    参数:
        cur: 数据库 cursor
        c (dict): 评论数据，包含 post_id, title, comment_id, commenter,
                  comment_content, like_count, comment_time
    """
    sql = """INSERT INTO social_comment (post_id,title,comment_id,commenter,comment_content,like_count,comment_time)
    VALUES(%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE like_count=VALUES(like_count), comment_content=VALUES(comment_content)"""
    cur.execute(sql, (c["post_id"],c["title"],c["comment_id"],c["commenter"],c["comment_content"],c["like_count"],c["comment_time"]))


# ============================================================
# 互动数据更新
# ============================================================

def update_engagement(cur, post_id, like_count, comment_count):
    """
    更新已有帖子的互动数据（点赞数、评论数）。

    仅更新 like_count 和 comment_count，不更新标题和内容，
    避免覆盖之前的完整内容。

    参数:
        cur: 数据库 cursor
        post_id (str): 帖子唯一ID
        like_count (int): 最新点赞数
        comment_count (int): 最新评论数
    """
    # 更新已有帖子的互动数据(点赞数、评论数)，不更新标题和内容
    sql = "UPDATE social_post SET like_count=%s, comment_count=%s WHERE post_id=%s AND site_name='Bluesky'"
    cur.execute(sql, (like_count, comment_count, post_id))


# ============================================================
# 图片处理
# ============================================================

def get_image_urls(record, did=None):
    """
    从 Bluesky 帖子记录中提取图片URL列表。

    Bluesky 的图片存储在 Blob 系统中，需要通过 XRPC 接口下载。
    URL 格式: https://bsky.social/xrpc/com.atproto.sync.getBlob?did=...&cid=...

    支持两种图片类型：
      1. 帖子内嵌图片 (embed.images) - 帖子中直接插入的图片
      2. 外链缩略图 (embed.external.thumb) - 外部链接的缩略图

    参数:
        record: Bluesky 帖子的 record 对象
        did (str): 帖子作者的 DID（Decentralized Identifier）

    返回值:
        list[str]: 图片URL列表
    """
    images = []
    if not (hasattr(record, 'embed') and record.embed):
        return images
    embed = record.embed
    base = "https://bsky.social/xrpc/com.atproto.sync.getBlob"
    # 1. 帖子内嵌图片 - Image.image 是 BlobRef
    if hasattr(embed, 'images') and embed.images:
        for img in embed.images:
            blob = getattr(img, 'image', None)
            if blob and hasattr(blob, 'ref') and hasattr(blob.ref, 'link'):
                cid = str(blob.ref.link)
                if did:
                    images.append(f"{base}?did={did}&cid={cid}")
    # 2. 外链缩略图 - embed.external.thumb 是 BlobRef
    if hasattr(embed, 'external') and embed.external:
        ext = embed.external
        thumb = getattr(ext, 'thumb', None)
        if thumb and hasattr(thumb, 'ref') and hasattr(thumb.ref, 'link'):
            cid = str(thumb.ref.link)
            if did:
                images.append(f"{base}?did={did}&cid={cid}")
    return images

def download_image(url, post_id, idx):
    """
    下载图片到本地，返回本地文件名。

    使用 MD5 哈希（前16位）作为文件名前缀，避免冲突。
    如果文件已存在则跳过下载。

    参数:
        url (str): 图片远程URL
        post_id (str): 帖子ID（用于生成唯一文件名）
        idx (int): 图片序号

    返回值:
        str: 本地文件名，下载失败返回 None
    """
    # 下载图片到本地，返回本地文件名
    post_id_hash = hashlib.md5(post_id.encode()).hexdigest()[:16]
    filename = f"{post_id_hash}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        return filename
    try:
        resp = requests.get(url, proxies=PROXIES, timeout=30)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        print(f"    [IMG] 下载: {filename} ({len(resp.content)} bytes)")
        return filename
    except Exception as e:
        print(f"    [IMG] 下载失败: {e}")
        return None

def save_images(cur, conn, post_id, image_urls):
    """
    保存图片记录到 social_post_image 表，并下载到本地。

    处理流程：
      1. 检查该帖子是否已有图片记录（避免重复）
      2. 逐个下载图片
      3. 插入 social_post_image 记录

    参数:
        cur: 数据库 cursor
        conn: 数据库连接
        post_id (str): 帖子ID
        image_urls (list[str]): 图片URL列表

    返回值:
        str: 第一张图片的本地路径（用于 social_post.image_url）
    """
    # 保存图片记录到 social_post_image 表，并下载到本地
    # 先检查是否已有图片记录
    cur.execute("SELECT COUNT(*) FROM social_post_image WHERE post_id=%s", (post_id,))
    if cur.fetchone()[0] > 0:
        return ""
    first_local = ""
    for idx, url in enumerate(image_urls):
        local_file = download_image(url, post_id, idx)
        local_path = f"sentiment/images/{local_file}" if local_file else ""
        if idx == 0:
            first_local = local_path
        cur.execute(
            "INSERT INTO social_post_image (post_id, image_url, local_path, idx) VALUES(%s, %s, %s, %s)",
            (post_id, url, local_path, idx)
        )
    conn.commit()
    return first_local


# ============================================================
# 线程解析（帖子 + 评论树）
# ============================================================

def parse_thread(node, root_uri, root_title, root_image, kw, display_kw, conn, cur, depth=0):
    """
    递归解析 Bluesky 帖子线程（帖子 + 回复树）。

    Bluesky 的帖子和评论组织为树形结构：
      root (主帖)
        ├── reply1 (一级回复)
        │   ├── reply1_1 (二级回复)
        │   └── reply1_2
        └── reply2

    处理逻辑：
      - 如果当前节点是主帖 (node.post.uri == root_uri) → 保存为帖子
      - 如果当前节点是回复 → 保存为评论
      - 递归处理子回复，深度限制为 DEPTH（默认3层）

    参数:
        node: Bluesky 线程节点对象
        root_uri (str): 主帖的 URI（用于关联评论）
        root_title (str): 主帖标题
        root_image (str): 主帖图片路径
        kw (str): 搜索关键词
        display_kw (str): 关键词显示名称
        conn: 数据库连接
        cur: 数据库 cursor
        depth (int): 当前递归深度，防止无限递归

    返回值:
        tuple or None: (is_new, is_updated) 如果是主帖则返回，否则返回 None
    """
    if not node or depth > DEPTH: return None
    result = None
    if hasattr(node, 'post'):
        p = node.post
        cid = p.uri
        ct = ""
        if hasattr(p.record, 'created_at') and p.record.created_at:
            try: ct = datetime.fromisoformat(str(p.record.created_at).replace("Z","+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            except: pass
        if root_uri == cid:
            # 主帖 - 提取图片URL并下载
            img_urls = get_image_urls(p.record, did=p.author.did)
            local_first = ""
            if img_urls:
                local_first = save_images(cur, conn, cid, img_urls)
                # image_url 存第一个图片的本地路径用于快速访问
                root_image = local_first if local_first else ",".join(img_urls)
            is_new, is_updated = save_social_post(cur, {"uuid":str(uuid.uuid4()),"site_name":"Bluesky","trigger_keyword":display_kw,"source_board":"search",
                "post_id":cid,"title":(p.record.text or "")[:100],"author":p.author.handle,"publish_time":ct,
                "like_count":p.like_count or 0,"comment_count":p.reply_count or 0,"content":p.record.text or "",
                "original_url":f"https://bsky.app/profile/{p.author.handle}/post/{cid.split('/')[-1]}",
                "image_url":root_image})
            result = (is_new, is_updated)
            conn.commit()
            print(f"  [POST] {p.author.handle}: {(p.record.text or '')[:60]}")
        else:
            # 回复（评论）- 保存到 social_comment 表
            save_comment(cur, {"post_id":root_uri,"title":root_title,"comment_id":cid,"commenter":p.author.handle,
                "comment_content":p.record.text or "","like_count":p.like_count or 0,"comment_time":ct})
            conn.commit()
    # 递归处理子回复
    if hasattr(node, 'replies') and node.replies:
        for r in node.replies:
            child_result = parse_thread(r, root_uri, root_title, root_image, kw, display_kw, conn, cur, depth+1)
            if child_result and not result:
                result = child_result
    return result


# ============================================================
# 爬虫主流程
# ============================================================

def crawl(keywords, max_per_kw):
    """
    爬虫主流程：使用 atproto API 搜索 Bluesky 帖子。

    流程说明：
      1. 登录 Bluesky 账号
      2. 遍历每个关键词，调用 search_posts API 搜索
      3. 按图片优先级排序（有图帖子优先处理）
      4. 对每个帖子：
         - 如果已存在 → 只更新互动数据（点赞/评论数）+ 更新评论详情
         - 如果不存在 → 完整保存帖子 + 下载图片 + 保存评论
      5. 使用 get_post_thread 获取完整线程（帖子+回复树）

    参数:
        keywords (list[str]): 关键词列表
        max_per_kw (int): 每个关键词最多保存的帖子数

    返回值:
        tuple: (items_found, items_new, items_updated)
    """
    print("=== Bluesky 爬虫 ===")
    client = Client()
    client.login(BSKY_USERNAME, BSKY_PASSWORD)
    conn = get_db(); cur = conn.cursor()
    items_found = 0
    items_new = 0
    items_updated = 0
    try:
        for kw in keywords:
            display_kw = KEYWORD_DISPLAY.get(kw, kw)
            print(f"\n--- 搜索: {kw} ---")
            # 调用 Bluesky API 搜索帖子
            result = client.app.bsky.feed.search_posts({"q": kw, "limit": max_per_kw*6, "sort": "latest"})

            # 按媒体类型排序：有图片的帖子优先（embed.images > embed.external.thumb > 无图）
            def has_media(post):
                embed = getattr(post.record, 'embed', None)
                if not embed: return 0
                if hasattr(embed, 'images') and embed.images: return 2
                if hasattr(embed, 'external') and getattr(embed.external, 'thumb', None): return 1
                return 0

            sorted_posts = sorted(result.posts, key=has_media, reverse=True)
            cnt = 0
            for post in sorted_posts:
                if cnt >= max_per_kw: break
                items_found += 1
                uri = post.uri
                title = (post.record.text or "")[:100]
                # 如果是回复帖，取其根帖子的 URI
                if hasattr(post.record,'reply') and post.record.reply: uri = post.record.reply.root.uri
                # 检查帖子是否已存在
                cur.execute("SELECT 1 FROM social_post WHERE post_id=%s",(uri,))
                exists = cur.fetchone()
                try:
                    # 获取完整帖子线程（帖子+评论树）
                    th = client.app.bsky.feed.get_post_thread({"uri":uri,"depth":DEPTH})
                    if exists:
                        # 已有帖子：只更新互动数据(点赞/评论数)，同时更新评论详情
                        post_obj = th.thread.post if th.thread else None
                        if post_obj:
                            update_engagement(cur, uri, post_obj.like_count or 0, post_obj.reply_count or 0)
                            parse_thread(th.thread, uri, title, "", kw, display_kw, conn, cur)
                            conn.commit()
                            items_updated += 1
                            print(f"  [UPDATE] {(post_obj.record.text or '')[:60]} (like:{post_obj.like_count or 0} reply:{post_obj.reply_count or 0})")
                    else:
                        # 新帖子：完整保存
                        parse_result = parse_thread(th.thread, uri, title, "", kw, display_kw, conn, cur)
                        if parse_result:
                            is_new, is_updated = parse_result
                            if is_new: items_new += 1
                            if is_updated: items_updated += 1
                    cnt += 1
                except Exception as e: print(f"  [ERR] {e}")
                time.sleep(1)
            print(f"  {kw}: {cnt}条")
    finally: cur.close(); conn.close()
    print("=== 完成 ===")
    return items_found, items_new, items_updated


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """
    解析命令行参数。

    参数说明:
        --config-id: 爬取配置ID（由调度系统传入）
        --keyword:   指定单个关键词（覆盖默认 ALL_KEYWORDS）
        --max:       每个关键词最多爬取的帖子数（覆盖 DEFAULT_MAX_PER_KW）
        --log-id:    爬取日志ID（用于更新日志状态）
        max_legacy:  位置参数（向后兼容旧版本）

    返回值:
        argparse.Namespace: 解析后的参数对象
    """
    parser = argparse.ArgumentParser(description="Bluesky Spider")
    parser.add_argument("--config-id", type=int, default=None, help="crawl_config ID")
    parser.add_argument("--keyword", type=str, default=None, help="Keyword to crawl (overrides default list)")
    parser.add_argument("--max", type=int, default=None, help="Max results per keyword (overrides default)")
    parser.add_argument("--log-id", type=int, default=None, help="crawl_log ID to update")
    # 向后兼容：允许位置参数作为 max_per_kw
    parser.add_argument("max_legacy", nargs="?", type=int, default=None, help="(legacy) max per keyword")
    return parser.parse_args()

def main():
    """
    主函数：解析参数 → 执行爬虫 → 更新日志。

    负责：
      1. 解析命令行参数
      2. 确定关键词列表（--keyword 指定单个关键词，否则使用 ALL_KEYWORDS）
      3. 确定每个关键词最多爬取的帖子数
      4. 记录爬取开始时间
      5. 调用 crawl() 执行爬虫
      6. 成功时更新日志和配置
      7. 失败时记录错误信息
    """
    args = parse_args()

    # 确定关键词列表
    if args.keyword:
        keywords = [args.keyword]
    else:
        keywords = ALL_KEYWORDS

    # 确定每个关键词最多爬取的帖子数（优先 --max，其次 --max_legacy，最后默认值）
    max_per_kw = args.max if args.max is not None else (args.max_legacy if args.max_legacy is not None else DEFAULT_MAX_PER_KW)

    config_id = args.config_id
    log_id = args.log_id

    # 记录爬取开始时间
    update_crawl_log_start(log_id)

    try:
        items_found, items_new, items_updated = crawl(keywords, max_per_kw)
        # 成功：更新日志和配置
        update_crawl_log(log_id, items_found, items_new, items_updated)
        update_config_last_crawl(config_id)
    except Exception as e:
        # 失败：记录错误信息
        update_crawl_log_error(log_id, str(e))
        raise

if __name__ == "__main__":
    main()
