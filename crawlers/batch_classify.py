#!/usr/bin/env python3
"""
批量分类脚本：为所有未分类的事件打上分类标签 + 生成预摘要
=========================================================

策略：
- 所有未分类事件统一调用4B模型，同时生成摘要和分类
- 使用与AiSummaryGenerator.callPreSummarize完全一致的提示词
- 仅更新 category 和 pre_summary 字段，不更新 crawl_time

使用方式：
    python3 batch_classify.py              # 正常运行
    python3 batch_classify.py --dry-run    # 只统计不修改
    python3 batch_classify.py --limit 10   # 只处理前10条（测试用）
    python3 batch_classify.py --source post # 只处理社交帖子
    python3 batch_classify.py --source news # 只处理新闻
"""

import os
import sys
import re
import time
import json
import urllib.request
import argparse
import pymysql
from crawler_config import DB

# ============================================================
# 配置（与AiSummaryGenerator一致）
# ============================================================
OLLAMA_URL = "http://200m.frpee.com:18138/api/chat"
MODEL = "qwen3.5:4b-q4_K_M"

EVENT_CATEGORIES = [
    "军事", "贸易", "外交", "科技", "人权",
    "社会", "经济", "政治", "台海", "港澳",
    "南海", "网络安全", "军售", "制裁", "能源",
    "教育", "环境", "金融", "移民", "其他"
]
CAT_LIST = "、".join(EVENT_CATEGORIES)

SYSTEM_PROMPT = "你是摘要和分类助手。严格按格式输出：第一行摘要，第二行分类标签。分类只能是提供的选项之一。不要输出其他内容。"


def build_prompt(text, content_type):
    """构建与callPreSummarize一致的提示词"""
    if content_type == "news":
        return (
            "请为以下新闻生成100字以内的核心摘要。\n"
            "最后一行必须输出分类标签。可选分类：" + CAT_LIST + "\n"
            "严格按以下格式输出（仅两行，不要多余内容）：\n"
            "第一行：摘要文本（不要包含分类字样）\n"
            "第二行：分类: 以上可选分类中的一个\n\n"
            "示例：\n"
            "中国商务部宣布对欧盟进口猪肉征收反倾销税，影响全球肉类贸易格局。\n"
            "分类: 贸易\n\n"
            "以下是待分析内容：\n\n" + text
        )
    else:
        return (
            "请为以下帖子生成80字以内的核心摘要。\n"
            "最后一行必须输出分类标签。可选分类：" + CAT_LIST + "\n"
            "严格按以下格式输出（仅两行，不要多余内容）：\n"
            "第一行：摘要文本（不要包含分类字样）\n"
            "第二行：分类: 以上可选分类中的一个\n\n"
            "示例：\n"
            "博主讨论台湾半导体产业现状及大陆芯片制裁影响。\n"
            "分类: 科技\n\n"
            "以下是待分析内容：\n\n" + text
        )


def parse_pre_summary_result(raw):
    """解析4B模型输出，提取摘要和分类（与parsePreSummaryResult一致）"""
    if not raw:
        return "", "其他"

    category = "其他"
    # 提取分类
    cat_match = re.search(r"分类[:：]\s*([^\s,，。；]+)", raw)
    if cat_match:
        cat = cat_match.group(1).strip().rstrip("。；，, ")
        if cat in EVENT_CATEGORIES:
            category = cat

    # 提取摘要文本（去掉分类行）
    summary = raw
    summary = re.sub(r"^分类[:：]\s*\S+\s*$", "", summary, flags=re.MULTILINE).strip()
    summary = re.sub(r"分类[:：]\s*\S+\s*$", "", summary).strip()
    # 去掉"摘要："等前缀
    summary = re.sub(r"^(摘要|摘要文本|总结)[：:]\s*", "", summary)
    summary = re.sub(r"\n{3,}", "\n\n", summary).strip()

    return summary, category


def call_ollama(prompt):
    """调用4B模型（与callPreSummarize一致）"""
    body = {
        "model": MODEL,
        "keep_alive": -1,
        "think": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "stream": False
    }

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            return result.get("message", {}).get("content", "").strip()
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  [ERROR] LLM调用失败(3次): {e}")
                return None


def main():
    parser = argparse.ArgumentParser(description="批量分类+预摘要")
    parser.add_argument("--dry-run", action="store_true", help="只统计不修改")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量")
    parser.add_argument("--source", choices=["all", "post", "news"], default="all")
    args = parser.parse_args()

    conn = pymysql.connect(**DB)
    cur = conn.cursor(pymysql.cursors.DictCursor)

    # 统计
    cur.execute("""
        SELECT 'post' as src, COUNT(*) as cnt FROM social_post WHERE category IS NULL OR category = ''
        UNION ALL
        SELECT 'news', COUNT(*) FROM news_article WHERE category IS NULL OR category = ''
    """)
    stats = cur.fetchall()
    total = sum(r["cnt"] for r in stats)
    print(f"未分类事件总数: {total}")
    for r in stats:
        print(f"  {r['src']}: {r['cnt']}")

    if args.dry_run:
        print("\n[dry-run] 不执行修改")
        cur.close(); conn.close()
        return

    processed = 0
    success = 0
    failed = 0
    start_time = time.time()

    # ===== 处理社交帖子 =====
    if args.source in ("all", "post"):
        print("\n=== 处理社交帖子 ===")
        cur.execute("""
            SELECT post_id, title, content, pre_summary, site_name
            FROM social_post 
            WHERE category IS NULL OR category = ''
            ORDER BY crawl_time
        """ + (f"LIMIT {args.limit}" if args.limit > 0 else ""))
        posts = cur.fetchall()
        print(f"待处理: {len(posts)} 条")

        for i, post in enumerate(posts):
            if args.limit > 0 and processed >= args.limit:
                break

            title = post.get("title") or ""
            content = post.get("content") or ""
            text = f"{title}\n{content}" if content else title

            prompt = build_prompt(text, "post")
            raw = call_ollama(prompt)
            if raw is None:
                failed += 1
                continue

            summary, category = parse_pre_summary_result(raw)

            # 仅更新 category 和 pre_summary，不更新 crawl_time
            if summary:
                cur.execute(
                    "UPDATE social_post SET category = %s, pre_summary = %s WHERE post_id = %s",
                    (category, summary, post["post_id"])
                )
            else:
                cur.execute(
                    "UPDATE social_post SET category = %s WHERE post_id = %s",
                    (category, post["post_id"])
                )

            success += 1
            processed += 1
            time.sleep(0.2)  # 防止请求过快

            if processed % 50 == 0:
                conn.commit()
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                remaining = (len(posts) - i - 1) / speed if speed > 0 else 0
                print(f"  [{processed}/{len(posts)}] 成功:{success} 失败:{failed} | {speed:.1f}条/秒 | 剩余:{remaining/60:.1f}分钟")

        conn.commit()

    # ===== 处理新闻 =====
    if args.source in ("all", "news"):
        print("\n=== 处理新闻 ===")
        cur.execute("""
            SELECT id, title, content, pre_summary, source
            FROM news_article 
            WHERE category IS NULL OR category = ''
            ORDER BY id
        """ + (f"LIMIT {args.limit}" if args.limit > 0 else ""))
        news = cur.fetchall()
        print(f"待处理: {len(news)} 条")

        for i, n in enumerate(news):
            if args.limit > 0 and processed >= args.limit:
                break

            title = n.get("title") or ""
            content = n.get("content") or ""
            text = f"{title}\n{content}" if content else title

            prompt = build_prompt(text, "news")
            raw = call_ollama(prompt)
            if raw is None:
                failed += 1
                continue

            summary, category = parse_pre_summary_result(raw)

            if summary:
                cur.execute(
                    "UPDATE news_article SET category = %s, pre_summary = %s WHERE id = %s",
                    (category, summary, n["id"])
                )
            else:
                cur.execute(
                    "UPDATE news_article SET category = %s WHERE id = %s",
                    (category, n["id"])
                )

            success += 1
            processed += 1
            time.sleep(0.2)

            if processed % 50 == 0:
                conn.commit()

        conn.commit()

    elapsed = time.time() - start_time
    print(f"\n=== 完成 ===")
    print(f"总计: {processed} 条 | 成功: {success} | 失败: {failed}")
    print(f"耗时: {elapsed/60:.1f} 分钟 | 速度: {processed/elapsed:.1f}条/秒" if elapsed > 0 else "")

    # 显示分类统计
    print("\n=== 分类统计 ===")
    cur.execute("""
        SELECT category, COUNT(*) as cnt FROM (
            SELECT category FROM social_post WHERE category IS NOT NULL AND category != ''
            UNION ALL
            SELECT category FROM news_article WHERE category IS NOT NULL AND category != ''
        ) t GROUP BY category ORDER BY cnt DESC
    """)
    for row in cur.fetchall():
        print(f"  {row['category']}: {row['cnt']}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
