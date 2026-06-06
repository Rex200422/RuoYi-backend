"""
RuoYi 舆情爬虫 - 共享数据库模块
所有爬虫共用的数据库连接、保存函数和日志更新函数。
所有 save 函数通过 cursor.rowcount 判断操作类型：
  rowcount=1 → INSERT 新增
  rowcount=2 → UPDATE 更新（ON DUPLICATE KEY UPDATE 命中）
  rowcount=0 → 值未变化（既非新增也更新）
"""
import pymysql

# ============================================================
# 数据库配置
# ============================================================
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "200422",
    "database": "ry-vue",
    "charset": "utf8mb4",
}


def get_db():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


def clean(text):
    """清理文本中的多余空白"""
    import re
    return re.sub(r"\s+", " ", text).strip() if text else ""


# ============================================================
# 新闻文章保存
# ============================================================
def save_news_article(cursor, article):
    """
    保存新闻文章到 news_article 表。
    基于 url 唯一索引去重（INSERT ... ON DUPLICATE KEY UPDATE）。
    :param cursor: 已有的数据库 cursor
    :param article: dict，包含 title, url, publish_date, keywords, cover_image, content, source
    :return: (is_new: bool, is_updated: bool)
        - is_new=True  : 新增了一条记录 (rowcount=1)
        - is_updated=True: 更新了已有记录 (rowcount=2)
        - 都为 False   : 值未变化 (rowcount=0)
    """
    sql = """INSERT INTO news_article (title, url, publish_date, keywords, cover_image, content, source)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        title=VALUES(title),
        publish_date=VALUES(publish_date),
        keywords=VALUES(keywords),
        cover_image=VALUES(cover_image),
        content=VALUES(content)"""
    cursor.execute(sql, (
        article["title"],
        article["url"],
        article.get("publish_date", article.get("date", "")),
        article.get("keywords", ""),
        article.get("cover_image", ""),
        article.get("content", ""),
        article.get("source", ""),
    ))
    rc = cursor.rowcount
    return rc == 1, rc == 2


# ============================================================
# 社交帖子保存
# ============================================================
def save_social_post(cursor, post):
    """
    保存社交帖子到 social_post 表。
    基于 post_id 唯一索引去重。
    :param cursor: 已有的数据库 cursor
    :param post: dict，包含 uuid, site_name, trigger_keyword, source_board, post_id,
                 title, author, publish_time, like_count, comment_count, content,
                 original_url, image_url
    :return: (is_new: bool, is_updated: bool)
    """
    sql = """INSERT INTO social_post
    (uuid, site_name, trigger_keyword, source_board, post_id,
     title, author, publish_time, like_count, comment_count,
     content, original_url, image_url)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        like_count=VALUES(like_count),
        comment_count=VALUES(comment_count),
        title=VALUES(title),
        content=VALUES(content),
        image_url=VALUES(image_url)"""
    cursor.execute(sql, (
        post["uuid"],
        post["site_name"],
        post.get("trigger_keyword", ""),
        post.get("source_board", ""),
        post["post_id"],
        post.get("title", ""),
        post.get("author", ""),
        post.get("publish_time", ""),
        post.get("like_count", 0),
        post.get("comment_count", 0),
        post.get("content", ""),
        post.get("original_url", ""),
        post.get("image_url", ""),
    ))
    rc = cursor.rowcount
    return rc == 1, rc == 2


# ============================================================
# 社交评论保存
# ============================================================
def save_social_comment(cursor, comment):
    """
    保存评论到 social_comment 表。
    基于 comment_id 唯一索引去重。
    :param cursor: 已有的数据库 cursor
    :param comment: dict，包含 post_id, title, comment_id, commenter,
                    comment_content, like_count, comment_time
    :return: (is_new: bool, is_updated: bool)
    """
    sql = """INSERT INTO social_comment
    (post_id, title, comment_id, commenter, comment_content, like_count, comment_time)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        like_count=VALUES(like_count),
        comment_content=VALUES(comment_content)"""
    cursor.execute(sql, (
        comment["post_id"],
        comment.get("title", ""),
        comment["comment_id"],
        comment.get("commenter", ""),
        comment.get("comment_content", ""),
        comment.get("like_count", 0),
        comment.get("comment_time", ""),
    ))
    rc = cursor.rowcount
    return rc == 1, rc == 2


# ============================================================
# 爬取日志更新（独立连接版本，适合在爬取开始/结束时调用）
# ============================================================
def update_crawl_log_start(log_id):
    """更新爬取日志：记录开始时间"""
    if not log_id:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE crawl_log SET start_time=NOW() WHERE id=%s", (log_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def update_crawl_log(log_id, items_found, items_new, items_updated):
    """
    更新爬取日志：成功完成。
    :param log_id: crawl_log 表的主键 ID
    :param items_found: 爬取发现的总条目数
    :param items_new: 新增条目数（INSERT rowcount=1 的累计）
    :param items_updated: 更新条目数（UPDATE rowcount=2 的累计）
    """
    if not log_id:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE crawl_log
               SET status='success', end_time=NOW(),
                   items_found=%s, items_new=%s, items_updated=%s
               WHERE id=%s""",
            (items_found, items_new, items_updated, log_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def update_crawl_log_error(log_id, error_msg):
    """更新爬取日志：记录错误信息"""
    if not log_id:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE crawl_log SET status='failed', end_time=NOW(), error_msg=%s WHERE id=%s",
            (str(error_msg)[:2000], log_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def update_config_last_crawl(config_id):
    """更新爬取配置：记录最后爬取时间"""
    if not config_id:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE crawl_config SET last_crawl_time=NOW() WHERE id=%s", (config_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
