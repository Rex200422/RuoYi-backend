#!/usr/bin/env python3
"""
RuoYi 舆情平台 - AI 舆情简报生成器
从数据库取最近时间段内的新闻和社交帖子数据，
调用 Ollama qwen3.6 生成舆情简报，存入 ai_summary 表。

用法：
    python3 ai_summarizer.py              # 生成最近1小时简报并保存
    python3 ai_summarizer.py --hours 2    # 指定时间窗口
    python3 ai_summarizer.py --dry-run    # 只输出不保存
"""
import os
import sys
import time
import argparse
from datetime import datetime, timedelta

import pymysql
import requests

# ============================================================
# 配置
# ============================================================
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "200422",
    "database": "ry-vue",
    "charset": "utf8mb4",
}

OLLAMA_BASE = "http://200m.frpee.com:18138"
MODEL_NAME = "qwen3.6:latest"

# 确保不走代理直连 Ollama
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# 预处理限制
MAX_NEWS = 25          # 最多取多少条新闻（给帖子/评论留空间）
MAX_POSTS = 80         # 最多取多少条帖子（按点赞排序，热门优先）
MAX_CONTENT_LEN = 150  # 新闻内容截取前 N 字（精简）
MAX_COMMENTS = 30      # 最多取多少条评论（按点赞排序，热门优先）
ESTIMATED_TOKEN_PER_CHAR = 1.5  # 中文约 1.5 token/字符（粗估）
MAX_INPUT_TOKENS = 28000        # 上下文 token 上限（模型32k，留4k缓冲）


def get_db():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


# ============================================================
# 1. 获取上次生成时间
# ============================================================
def get_last_summary_time():
    """
    从 ai_summary 表取最近一条记录的 create_time。
    如果没有历史记录，返回 24 小时前（避免首次拉取数据过多）。
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT create_time FROM ai_summary ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        if row:
            return row[0]
        return datetime.now() - timedelta(hours=24)
    finally:
        conn.close()


# ============================================================
# 2. 查询新数据
# ============================================================
def fetch_news(since):
    """取 since 之后的新闻"""
    conn = get_db()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT title, source, keywords, content, publish_date "
            "FROM news_article WHERE crawl_time > %s ORDER BY crawl_time DESC",
            (since,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def fetch_social_posts(since):
    """取 since 之后的社交帖子"""
    conn = get_db()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT title, author, site_name, like_count, comment_count, "
            "content, trigger_keyword "
            "FROM social_post WHERE crawl_time > %s ORDER BY like_count DESC",
            (since,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def fetch_social_comments(since):
    """取 since 之后的社交评论，关联帖子信息"""
    conn = get_db()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT sc.post_id, sc.commenter, sc.comment_content, sc.like_count as comment_likes, "
            "sc.comment_time, sp.title as post_title, sp.author as post_author, sp.site_name "
            "FROM social_comment sc "
            "JOIN social_post sp ON sc.post_id = sp.post_id "
            "WHERE sc.crawl_time > %s "
            "ORDER BY sc.like_count DESC, sc.crawl_time DESC",
            (since,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def fetch_previous_summary():
    """获取上一次简报的核心信息，用于对比舆情变化"""
    conn = get_db()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(
            "SELECT title, risk_level, content, news_count, social_count, "
            "DATE_FORMAT(create_time, '%m-%d %H:%M') as create_time "
            "FROM ai_summary ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()


# ============================================================
# 3. 数据预处理（控制上下文长度）
# ============================================================
def estimate_tokens(text):
    """粗略估算中文 token 数"""
    if not text:
        return 0
    return int(len(text) * ESTIMATED_TOKEN_PER_CHAR)


def preprocess_news(news_list):
    """
    预处理新闻：
    - 截取 title + 前 MAX_CONTENT_LEN 字内容
    - 限制数量
    """
    if len(news_list) > MAX_NEWS:
        news_list = news_list[:MAX_NEWS]

    items = []
    for n in news_list:
        title = n.get("title", "") or ""
        content = (n.get("content", "") or "")[:MAX_CONTENT_LEN]
        source = n.get("source", "") or ""
        keywords = n.get("keywords", "") or ""
        parts = [f"【{source}】{title}"]
        if keywords:
            parts.append(f"关键词: {keywords}")
        if content:
            parts.append(content)
        items.append("\n".join(parts))
    return items


def preprocess_posts(post_list):
    """
    预处理社交帖子：
    - 取 title + author + like_count + comment_count + content摘要
    - 互动数据越丰富的帖子越靠前
    - 限制数量
    """
    if len(post_list) > MAX_POSTS:
        post_list = post_list[:MAX_POSTS]

    items = []
    for p in post_list:
        title = (p.get("title", "") or "")[:100]
        author = p.get("author", "") or ""
        site = p.get("site_name", "") or ""
        likes = p.get("like_count", 0) or 0
        comments = p.get("comment_count", 0) or 0
        keyword = p.get("trigger_keyword", "") or ""
        content = (p.get("content", "") or "")[:150]
        engagement = f"{likes}赞 {comments}评"
        parts = [f"[{site}] @{author}: {title}"]
        parts.append(f"  互动: {engagement} | 关键词: {keyword}")
        if content and content != title:
            parts.append(f"  内容: {content}")
        items.append("\n".join(parts))
    return items


def preprocess_comments(comment_list, max_comments=30):
    """
    预处理社交评论：
    - 按点赞数排序，取热门评论
    - 每条评论关联原帖信息
    - 控制数量避免占用过多上下文
    """
    if len(comment_list) > max_comments:
        comment_list = comment_list[:max_comments]

    items = []
    for c in comment_list:
        commenter = c.get("commenter", "") or ""
        content = (c.get("comment_content", "") or "")[:150]
        likes = c.get("comment_likes", 0) or 0
        post_title = (c.get("post_title", "") or "")[:60]
        site = c.get("site_name", "") or ""
        parts = [f"[{site}] {commenter} (👍{likes})"]
        parts.append(f"  评论: {content}")
        parts.append(f"  原帖: {post_title}")
        items.append("\n".join(parts))
    return items


def build_data_section(news_items, post_items, comment_items, prev_summary):
    """
    拼接数据段，按比例分配 token 预算：
    - 新闻 40%，帖子 35%，评论 15%，前次摘要 10%
    返回 (data_text, actual_news_count, actual_post_count, actual_comment_count)
    """
    sections = []
    total_tokens = 0

    # token 预算分配
    news_budget = int(MAX_INPUT_TOKENS * 0.40)
    post_budget = int(MAX_INPUT_TOKENS * 0.35)
    comment_budget = int(MAX_INPUT_TOKENS * 0.15)
    prev_budget = int(MAX_INPUT_TOKENS * 0.10)

    actual_news = 0
    actual_posts = 0
    actual_comments = 0

    # 新闻段（占 40% 预算）
    if news_items:
        sections.append("=== 新闻文章 ===")
        news_tokens = 0
        for item in news_items:
            t = estimate_tokens(item)
            if news_tokens + t > news_budget:
                break
            sections.append(item)
            news_tokens += t
            actual_news += 1
        total_tokens += news_tokens

    # 帖子段（占 35% 预算）
    if post_items:
        sections.append("\n=== 社交帖子 ===")
        post_tokens = 0
        for item in post_items:
            t = estimate_tokens(item)
            if post_tokens + t > post_budget:
                break
            sections.append(item)
            post_tokens += t
            actual_posts += 1
        total_tokens += post_tokens

    # 评论段（占 15% 预算）
    if comment_items:
        sections.append("\n=== 热门评论 ===")
        comment_tokens = 0
        for item in comment_items:
            t = estimate_tokens(item)
            if comment_tokens + t > comment_budget:
                break
            sections.append(item)
            comment_tokens += t
            actual_comments += 1
        total_tokens += comment_tokens

    # 前次摘要段（占 10% 预算）
    if prev_summary:
        prev_title = prev_summary.get("title", "") or ""
        prev_risk = prev_summary.get("risk_level", "") or ""
        prev_time = prev_summary.get("create_time", "") or ""
        prev_news = prev_summary.get("news_count", 0) or 0
        prev_social = prev_summary.get("social_count", 0) or 0
        prev_content = prev_summary.get("content", "") or ""
        # 提取前次摘要的核心摘要部分
        prev_summary_text = ""
        lines = prev_content.split("\\n")
        in_summary = False
        for line in lines:
            if "## 2" in line or "核心摘要" in line:
                in_summary = True
                continue
            if in_summary and (line.startswith("## 3") or line.startswith("## ")):
                break
            if in_summary:
                prev_summary_text += line + "\\n"
        if not prev_summary_text.strip():
            prev_summary_text = prev_content[:500]

        prev_section = (
            f"\n=== 上次简报 (时间: {prev_time}) ===\n"
            f"标题: {prev_title}\n"
            f"风险等级: {prev_risk}\n"
            f"数据量: {prev_news}条新闻 + {prev_social}条社交\n"
            f"核心摘要: {prev_summary_text.strip()[:500]}"
        )
        t = estimate_tokens(prev_section)
        if t <= prev_budget:
            sections.append(prev_section)
            total_tokens += t

    return "\n".join(sections), actual_news, actual_posts, actual_comments


# ============================================================
# 4. 调用 Ollama 生成简报
# ============================================================
SYSTEM_PROMPT = """你是一个专业的舆情分析师，服务于一个舆情监测平台。你的任务是根据提供的实时抓取数据，生成一份专业的舆情简报。"""


USER_PROMPT_TEMPLATE = """请根据以下过去 {hours} 小时内抓取的舆情数据，生成一份专业的舆情监测简报。

数据概览：{news_count} 条新闻，{social_count} 条社交帖子，{comment_count} 条热门评论。
{prev_section}
数据内容：
{data_section}

请严格按照以下格式输出 Markdown 简报：

## 1. 标题
用一句话概括本时段舆情态势

## 2. 核心摘要
3-5 句话总结最重要的事件和趋势

## 3. 分类统计
按主题分类（军事、贸易、人权、外交、科技、社会等），每个分类列出关键事件，格式：
### 分类名
- 事件1（来源）
- 事件2（来源）

## 4. 热门互动分析
分析本时段内点赞/评论互动最高的帖子和评论，总结舆论焦点

## 5. 舆情变化对比
与上一次简报对比，分析风险趋势变化（升高/持平/下降），新增的重要议题

## 6. 风险评级
**评级：低/中/高**

说明理由

## 7. 关注建议
下一步需要重点关注的方向（3-5 条）

请用中文输出。"""


def check_ollama():
    """检查 Ollama 是否可用"""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
        r.raise_for_status()
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
        print(f"[ai_summarizer] Ollama 可用，模型: {models}")
        return True
    except requests.exceptions.ConnectionError:
        print("[ai_summarizer] ❌ Ollama 连接失败，请检查服务是否运行")
        return False
    except Exception as e:
        print(f"[ai_summarizer] ❌ Ollama 检查失败: {e}")
        return False


def call_ollama(prompt):
    """
    调用 Ollama API 生成简报。
    返回 (response_text, elapsed_seconds) 或 (None, 0)。
    """
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    try:
        start = time.time()
        r = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json=payload,
            timeout=300,
        )
        r.raise_for_status()
        data = r.json()
        elapsed = time.time() - start
        content = data.get("message", {}).get("content", "")
        tokens_out = data.get("eval_count", 0)
        print(f"[ai_summarizer] ✅ 生成完成 ({elapsed:.1f}s, {tokens_out} tokens)")
        return content, int(elapsed)
    except requests.exceptions.Timeout:
        print("[ai_summarizer] ❌ Ollama 请求超时 (300s)")
        return None, 0
    except Exception as e:
        print(f"[ai_summarizer] ❌ Ollama 调用失败: {e}")
        return None, 0


# ============================================================
# 5. 解析简报标题和风险等级
# ============================================================
def extract_title_and_risk(content):
    """
    从生成的简报中提取标题和风险等级。
    返回 (title, risk_level)
    """
    title = ""
    risk_level = "中"

    lines = content.split("\n")
    for line in lines:
        # 提取标题：找 ## 1. 标题 之后的第一行非空内容
        if "## 1" in line or "标题" in line:
            # 找下一个非空行
            idx = lines.index(line)
            for j in range(idx + 1, min(idx + 5, len(lines))):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("#"):
                    title = candidate
                    break

        # 提取风险评级
        if ("评级" in line or "风险" in line) and line.startswith("#"):
            line_upper = line.upper()
            if "高" in line_upper or "HIGH" in line_upper:
                risk_level = "高"
            elif "低" in line_upper or "LOW" in line_upper:
                risk_level = "低"
            else:
                risk_level = "中"

    # 如果提取不到标题，用默认
    if not title:
        title = f"舆情简报 {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    # 标题截取
    title = title[:200].strip().strip("*").strip("#").strip()

    return title, risk_level


# ============================================================
# 6. 存入数据库
# ============================================================
def save_summary(title, content, risk_level, data_start, data_end,
                 news_count, social_count, model_name, gen_seconds):
    """保存简报到 ai_summary 表"""
    sql = """INSERT INTO ai_summary
        (summary_type, title, content, risk_level,
         data_start, data_end, news_count, social_count,
         model_name, generate_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, (
            "hourly",
            title,
            content,
            risk_level,
            data_start,
            data_end,
            news_count,
            social_count,
            model_name,
            gen_seconds,
        ))
        conn.commit()
        summary_id = cur.lastrowid
        cur.close()
        print(f"[ai_summarizer] ✅ 简报已保存 (id={summary_id})")
        return summary_id
    finally:
        conn.close()


# ============================================================
# 7. 主流程
# ============================================================
def generate_summary(hours=1, dry_run=False):
    """
    生成舆情简报的完整流程。
    返回 (title, content, risk_level, generate_time) 或 None。
    """
    print(f"[ai_summarizer] === 开始生成简报 (时间窗口: {hours}h) ===")

    # 检查 Ollama
    if not check_ollama():
        print("[ai_summarizer] ⛔ Ollama 不可用，跳过生成")
        return None

    # 确定时间范围
    last_time = get_last_summary_time()
    now = datetime.now()
    data_end = now

    # 如果指定了 hours，用 now - hours 作为起点；否则用上次生成时间
    if hours:
        data_start = now - timedelta(hours=hours)
    else:
        data_start = last_time

    print(f"[ai_summarizer] 数据范围: {data_start} ~ {data_end}")

    # 拉取数据
    news_raw = fetch_news(data_start)
    posts_raw = fetch_social_posts(data_start)
    comments_raw = fetch_social_comments(data_start)
    prev_summary = fetch_previous_summary()
    print(f"[ai_summarizer] 新闻: {len(news_raw)} 条, 帖子: {len(posts_raw)} 条, 评论: {len(comments_raw)} 条")

    if not news_raw and not posts_raw:
        print("[ai_summarizer] ⚠️ 无新数据，跳过生成")
        return None

    # 预处理
    news_items = preprocess_news(news_raw)
    post_items = preprocess_posts(posts_raw)
    comment_items = preprocess_comments(comments_raw)
    data_section, actual_news, actual_posts, actual_comments = build_data_section(
        news_items, post_items, comment_items, prev_summary
    )
    print(
        f"[ai_summarizer] 预处理后: {actual_news} 条新闻, "
        f"{actual_posts} 条帖子, {actual_comments} 条评论, "
        f"预估 tokens: {estimate_tokens(data_section)}"
    )

    # 构建前次摘要提示
    prev_section = ""
    if prev_summary:
        prev_title = prev_summary.get("title", "") or ""
        prev_risk = prev_summary.get("risk_level", "") or ""
        prev_time = prev_summary.get("create_time", "") or ""
        prev_section = f"\n上次简报 ({prev_time}): {prev_title} [风险: {prev_risk}]"

    # 构建 prompt
    prompt = USER_PROMPT_TEMPLATE.format(
        hours=hours,
        news_count=actual_news,
        social_count=actual_posts,
        comment_count=actual_comments,
        prev_section=prev_section,
        data_section=data_section,
    )

    # 调用 Ollama
    content, gen_seconds = call_ollama(prompt)
    if not content:
        print("[ai_summarizer] ⛔ 生成失败")
        return None

    # 解析标题和风险等级
    title, risk_level = extract_title_and_risk(content)
    print(f"[ai_summarizer] 标题: {title}")
    print(f"[ai_summarizer] 风险等级: {risk_level}")

    if dry_run:
        print("\n" + "=" * 60)
        print("【DRY RUN - 以下简报未保存】")
        print("=" * 60)
        print(content)
        print("=" * 60)
    else:
        # 保存到数据库
        save_summary(
            title, content, risk_level,
            data_start, data_end,
            actual_news, actual_posts,
            MODEL_NAME, gen_seconds,
        )

    return (title, content, risk_level, gen_seconds)


# ============================================================
# 8. 命令行入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="AI 舆情简报生成器"
    )
    parser.add_argument(
        "--hours", type=int, default=1,
        help="时间窗口（小时），默认 1"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只输出不保存到数据库"
    )
    args = parser.parse_args()

    try:
        result = generate_summary(hours=args.hours, dry_run=args.dry_run)
        if result:
            title, content, risk_level, gen_seconds = result
            print(f"\n[ai_summarizer] === 完成 ===")
        else:
            print("\n[ai_summarizer] === 未生成简报 ===")
    except Exception as e:
        print(f"[ai_summarizer] ❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
