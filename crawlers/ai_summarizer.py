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
MAX_NEWS = 50          # 最多取多少条新闻
MAX_POSTS = 100        # 最多取多少条帖子
MAX_CONTENT_LEN = 200  # 新闻内容截取前 N 字
ESTIMATED_TOKEN_PER_CHAR = 1.5  # 中文约 1.5 token/字符（粗估）
MAX_INPUT_TOKENS = 20000        # 上下文 token 上限


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
            "FROM social_post WHERE crawl_time > %s ORDER BY crawl_time DESC",
            (since,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
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
    - 取 title + author + like_count + comment_count
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
        items.append(
            f"[{site}] @{author}: {title} "
            f"({likes}赞, {comments}评论, 关键词:{keyword})"
        )
    return items


def build_data_section(news_items, post_items):
    """
    拼接数据段，如果超过 token 限制则截断。
    返回 (data_text, actual_news_count, actual_post_count)
    """
    sections = []
    total_tokens = 0

    # 新闻段
    if news_items:
        sections.append("=== 新闻文章 ===")
        for item in news_items:
            entry = f"{item}"
            t = estimate_tokens(entry)
            if total_tokens + t > MAX_INPUT_TOKENS:
                break
            sections.append(entry)
            total_tokens += t

    # 帖子段
    if post_items:
        sections.append("\n=== 社交帖子 ===")
        for item in post_items:
            t = estimate_tokens(item)
            if total_tokens + t > MAX_INPUT_TOKENS:
                break
            sections.append(item)
            total_tokens += t

    # 统计实际放入的条数
    actual_news = 0
    actual_posts = 0
    in_posts = False
    for line in sections:
        if line == "=== 社交帖子 ===":
            in_posts = True
            continue
        if line.startswith("==="):
            continue
        if in_posts:
            actual_posts += 1
        else:
            actual_news += 1

    return "\n".join(sections), actual_news, actual_posts


# ============================================================
# 4. 调用 Ollama 生成简报
# ============================================================
SYSTEM_PROMPT = """你是一个专业的舆情分析师，服务于一个舆情监测平台。你的任务是根据提供的实时抓取数据，生成一份专业的舆情简报。"""


USER_PROMPT_TEMPLATE = """请根据以下过去 {hours} 小时内抓取的舆情数据，生成一份专业的舆情监测简报。

数据概览：{news_count} 条新闻，{social_count} 条社交帖子。

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

## 4. 风险评级
**评级：低/中/高**

说明理由

## 5. 关注建议
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
        if "评级" in line or "风险" in line:
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
    print(f"[ai_summarizer] 新闻: {len(news_raw)} 条, 帖子: {len(posts_raw)} 条")

    if not news_raw and not posts_raw:
        print("[ai_summarizer] ⚠️ 无新数据，跳过生成")
        return None

    # 预处理
    news_items = preprocess_news(news_raw)
    post_items = preprocess_posts(posts_raw)
    data_section, actual_news, actual_posts = build_data_section(
        news_items, post_items
    )
    print(
        f"[ai_summarizer] 预处理后: {actual_news} 条新闻, "
        f"{actual_posts} 条帖子, "
        f"预估 tokens: {estimate_tokens(data_section)}"
    )

    # 构建 prompt
    prompt = USER_PROMPT_TEMPLATE.format(
        hours=hours,
        news_count=actual_news,
        social_count=actual_posts,
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
