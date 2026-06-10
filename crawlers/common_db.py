"""
RuoYi 舆情爬虫 - 共享数据库模块
==================================

功能概述：
    所有爬虫（新闻爬虫、社交爬虫）共用的数据库操作模块。
    提供数据库连接、数据保存、日志更新等基础功能。

数据库配置：
    统一从 crawler_config.py 读取，无需在此模块中配置。
    使用 pymysql 连接 MySQL/MariaDB。

使用方式：
    from common_db import save_news_article, update_crawl_log

数据保存模式：
    所有 save_* 函数都采用 INSERT ... ON DUPLICATE KEY UPDATE 模式，
    即：新数据直接插入，已存在的数据则更新字段。
    返回值统一为 (is_new, is_updated) 元组。
"""
import pymysql
from crawler_config import DB


# ============================================================
# 数据库连接
# ============================================================

def get_db():
    """
    获取一个新的数据库连接。

    返回值:
        pymysql.Connection: 数据库连接对象，调用方需自行关闭连接。

    使用示例:
        conn = get_db()
        cur = conn.cursor()
        try:
            # 执行操作
            conn.commit()
        finally:
            cur.close()
            conn.close()
    """
    return pymysql.connect(**DB)


def clean(text):
    """
    清理文本中的多余空白字符。

    参数:
        text (str): 待清理的文本

    返回值:
        str: 将所有连续空白替换为单个空格后的文本，
             如果输入为空则返回空字符串。
    """
    import re  # 延迟导入，避免循环依赖
    return re.sub(r"\s+", " ", text).strip() if text else ""  # \s+ 匹配一个或多个空白字符


# ============================================================
# 新闻文章保存
# ============================================================

def save_news_article(cursor, article):
    """
    保存新闻文章到 news_article 表。

    使用 INSERT ... ON DUPLICATE KEY UPDATE 实现去重：
      - 如果 url 不存在 → 插入新记录，返回 (True, False)
      - 如果 url 已存在 → 更新 title/publish_date/keywords/cover_image/content，
        返回 (False, True)
      - 如果内容无变化（MySQL 优化跳过）→ 返回 (False, False)

    参数:
        cursor: 已有的数据库 cursor 对象（调用方需自行管理连接）
        article (dict): 文章数据，必须包含以下字段：
            - title (str): 文章标题
            - url (str): 文章URL（唯一索引，用于去重）
            可选字段：
            - publish_date / date (str): 发布日期
            - keywords (str): 关键词，逗号分隔
            - cover_image (str): 封面图本地路径
            - content (str): 文章正文HTML
            - source (str): 来源站点名

    返回值:
        tuple: (is_new: bool, is_updated: bool)
            - is_new=True: 新插入了一条记录
            - is_updated=True: 更新了一条已有记录
            - 两者都为False: 记录存在但内容无变化
    """
    sql = """INSERT INTO news_article (title, url, publish_date, keywords, cover_image, content, source)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        title=VALUES(title),
        publish_date=VALUES(publish_date),
        keywords=VALUES(keywords),
        cover_image=VALUES(cover_image),
        content=VALUES(content),
        crawl_time=NOW()"""  # 插入或更新新闻文章（更新crawl_time）
    cursor.execute(sql, (
        article["title"],
        article["url"],
        article.get("publish_date", article.get("date", "")),  # 兼容publish_date和date字段
        article.get("keywords", ""),
        article.get("cover_image", ""),
        article.get("content", ""),
        article.get("source", ""),
    ))
    rc = cursor.rowcount  # rowcount=1表示插入，2表示更新
    return rc == 1, rc == 2


# ============================================================
# 社交帖子保存
# ============================================================

def save_social_post(cursor, post):
    """
    保存社交帖子到 social_post 表。

    使用 INSERT ... ON DUPLICATE KEY UPDATE 实现去重：
      - 基于 post_id（帖子唯一ID）去重
      - 新帖子：完整插入所有字段
      - 已有帖子：更新 like_count、comment_count、title、content、image_url

    参数:
        cursor: 已有的数据库 cursor 对象
        post (dict): 帖子数据，必须包含以下字段：
            - uuid (str): 唯一标识（可用 uuid.uuid4() 生成）
            - site_name (str): 站点名，如 "Bluesky"、"Reddit"
            - post_id (str): 帖子唯一ID（唯一索引，用于去重）
        可选字段：
            - trigger_keyword (str): 触发搜索的关键词
            - source_board (str): 来源板块
            - title (str): 帖子标题
            - author (str): 作者
            - publish_time (str): 发布时间 "YYYY-MM-DD HH:MM:SS"
            - like_count (int): 点赞数
            - comment_count (int): 评论数
            - content (str): 帖子正文
            - original_url (str): 原始链接
            - image_url (str): 主图本地路径

    返回值:
        tuple: (is_new: bool, is_updated: bool)
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
        image_url=VALUES(image_url),
        crawl_time=NOW()"""  # 插入或更新社交帖子，基于post_id去重（更新crawl_time）
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
    rc = cursor.rowcount  # rowcount=1表示插入，2表示更新
    return rc == 1, rc == 2


# ============================================================
# 社交评论保存
# ============================================================

def save_social_comment(cursor, comment):
    """
    保存评论到 social_comment 表。

    使用 INSERT ... ON DUPLICATE KEY UPDATE 实现去重：
      - 基于 comment_id（评论唯一ID）去重
      - 新评论：完整插入
      - 已有评论：更新 like_count 和 comment_content

    参数:
        cursor: 已有的数据库 cursor 对象
        comment (dict): 评论数据，必须包含以下字段：
            - post_id (str): 所属帖子ID（外键，关联 social_post.post_id）
            - comment_id (str): 评论唯一ID（唯一索引，用于去重）
        可选字段：
            - title (str): 所属帖子标题
            - commenter (str): 评论者
            - comment_content (str): 评论内容
            - like_count (int): 点赞数
            - comment_time (str): 评论时间

    返回值:
        tuple: (is_new: bool, is_updated: bool)
    """
    sql = """INSERT INTO social_comment
    (post_id, title, comment_id, commenter, comment_content, like_count, comment_time)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        like_count=VALUES(like_count),
        comment_content=VALUES(comment_content)"""  # 插入或更新评论，基于comment_id去重
    cursor.execute(sql, (
        comment["post_id"],
        comment.get("title", ""),
        comment["comment_id"],
        comment.get("commenter", ""),
        comment.get("comment_content", ""),
        comment.get("like_count", 0),
        comment.get("comment_time", ""),
    ))
    rc = cursor.rowcount  # rowcount=1表示插入，2表示更新
    return rc == 1, rc == 2


# ============================================================
# 帖子图片保存
# ============================================================

def save_social_post_image(cursor, image):
    """
    保存帖子图片到 social_post_image 表。

    注意：此函数不做去重检查，直接插入新记录。
    如果同一 post_id 需要多张图片，每张图片对应一条记录。

    参数:
        cursor: 已有的数据库 cursor 对象
        image (dict): 图片数据，包含以下字段：
            - post_id (str): 所属帖子ID
            - image_url (str): 原始图片URL
            - local_path (str): 下载后的本地路径
            - idx (int): 图片序号（同一帖子内从0开始）

    返回值:
        bool: 是否成功插入（rowcount == 1）
    """
    sql = """INSERT INTO social_post_image (post_id, image_url, local_path, idx)
    VALUES (%s, %s, %s, %s)"""  # 插入图片记录，不做去重检查
    cursor.execute(sql, (
        image["post_id"],
        image.get("image_url", ""),
        image.get("local_path", ""),
        image.get("idx", 0),
    ))
    return cursor.rowcount == 1


# ============================================================
# 爬取日志更新（独立连接版本）
# ============================================================

def update_crawl_log_start(log_id):
    """
    更新爬取日志：记录开始爬取时间。

    在爬虫启动时调用，将 start_time 设为当前时间。
    如果 log_id 为 None 则跳过（允许无日志模式运行）。

    参数:
        log_id (int or None): crawl_log 表的记录ID，由调度系统传入
    """
    if not log_id:  # log_id为None时跳过
        return
    conn = get_db()  # 获取数据库连接
    cur = conn.cursor()  # 创建游标
    try:
        cur.execute("UPDATE crawl_log SET start_time=NOW() WHERE id=%s", (log_id,))  # 记录开始时间
        conn.commit()  # 提交事务
    finally:
        cur.close()  # 关闭游标
        conn.close()  # 关闭连接


def update_crawl_log(log_id, items_found, items_new, items_updated):
    """
    更新爬取日志：记录爬取成功完成的结果。

    参数:
        log_id (int or None): crawl_log 表的记录ID
        items_found (int): 本次爬取发现的文章/帖子总数
        items_new (int): 新增的记录数
        items_updated (int): 更新的记录数
    """
    if not log_id:  # log_id为None时跳过
        return
    conn = get_db()  # 获取数据库连接
    cur = conn.cursor()  # 创建游标
    try:
        cur.execute(  # 更新日志：状态为success，记录统计数据
            """UPDATE crawl_log
               SET status='success', end_time=NOW(),
                   items_found=%s, items_new=%s, items_updated=%s
               WHERE id=%s""",
            (items_found, items_new, items_updated, log_id),
        )
        conn.commit()  # 提交事务
    finally:
        cur.close()  # 关闭游标
        conn.close()  # 关闭连接


def update_crawl_log_error(log_id, error_msg):
    """
    更新爬取日志：记录爬取失败的错误信息。

    参数:
        log_id (int or None): crawl_log 表的记录ID
        error_msg (str): 错误信息，超过2000字符会被截断
    """
    if not log_id:  # log_id为None时跳过
        return
    conn = get_db()  # 获取数据库连接
    cur = conn.cursor()  # 创建游标
    try:
        cur.execute(  # 更新日志：状态为failed，记录错误信息
            "UPDATE crawl_log SET status='failed', end_time=NOW(), error_msg=%s WHERE id=%s",
            (str(error_msg)[:2000], log_id),  # 错误信息截断为2000字符
        )
        conn.commit()  # 提交事务
    finally:
        cur.close()  # 关闭游标
        conn.close()  # 关闭连接


def update_config_last_crawl(config_id):
    """
    更新爬取配置：记录最后一次爬取时间。

    在爬虫成功完成后调用，用于调度系统判断是否需要再次触发。

    参数:
        config_id (int or None): crawl_config 表的记录ID
    """
    if not config_id:  # config_id为None时跳过
        return
    conn = get_db()  # 获取数据库连接
    cur = conn.cursor()  # 创建游标
    try:
        cur.execute("UPDATE crawl_config SET last_crawl_time=NOW() WHERE id=%s", (config_id,))  # 更新最后爬取时间
        conn.commit()  # 提交事务
    finally:
        cur.close()  # 关闭游标
        conn.close()  # 关闭连接
