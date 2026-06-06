#!/usr/bin/env python3
"""
Ollama Qwen3.6 连通性 & 可用性测试脚本

测试内容：
1. API 连通性（模型列表）
2. 单轮对话生成
3. 舆情摘要生成能力（模拟真实场景）
"""

import json
import sys
import time
import requests

# ============================================================
# 配置
# ============================================================
OLLAMA_BASE = "http://200m.frpee.com:18138"
MODEL_NAME = "qwen3:8b"  # Ollama 默认模型名格式，实际以 api/tags 返回为准

# 代理配置（Ollama 服务器可能需要代理访问）
PROXIES = None  # 内网直连
# PROXIES = {"http": "http://192.168.0.14:7890/", "https": "http://192.168.0.14:7890/"}


def test_connectivity():
    """测试1: API 连通性"""
    print("=" * 50)
    print("测试1: API 连通性")
    print("=" * 50)
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", proxies=PROXIES, timeout=10)
        r.raise_for_status()
        data = r.json()
        models = data.get("models", [])
        print(f"✅ 连接成功！共 {len(models)} 个模型:")
        for m in models:
            size_gb = m.get("size", 0) / (1024**3)
            print(f"   📦 {m['name']} ({size_gb:.1f}GB)")
        if not models:
            print("⚠️  没有可用模型，请先 ollama pull 模型")
            return None
        # 优先选 qwen3
        chosen = None
        for m in models:
            if "qwen3" in m["name"].lower() or "qwen" in m["name"].lower():
                chosen = m["name"]
                break
        if not chosen:
            chosen = models[0]["name"]
        print(f"\n🎯 选用模型: {chosen}")
        return chosen
    except requests.exceptions.ConnectionError:
        print("❌ 连接失败: Connection refused — Ollama 服务未启动")
        return None
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return None


def test_single_chat(model):
    """测试2: 单轮对话"""
    print("\n" + "=" * 50)
    print("测试2: 单轮对话")
    print("=" * 50)
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "你好，请用一句话介绍你自己。"}
        ],
        "stream": False,
    }
    try:
        start = time.time()
        r = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, proxies=PROXIES, timeout=60)
        r.raise_for_status()
        data = r.json()
        elapsed = time.time() - start
        content = data.get("message", {}).get("content", "")
        tokens = data.get("eval_count", 0)
        print(f"✅ 响应 ({elapsed:.1f}s, {tokens} tokens):")
        print(f"   {content[:200]}")
        return True
    except Exception as e:
        print(f"❌ 对话失败: {e}")
        return False


def test_summary(model):
    """测试3: 舆情摘要生成（模拟真实场景）"""
    print("\n" + "=" * 50)
    print("测试3: 舆情摘要生成")
    print("=" * 50)

    # 模拟一批真实抓取数据
    sample_events = """
以下是过去1小时内抓取的舆情事件：

1. [Bluesky] @user1: China's new semiconductor sanctions will impact global chip supply chain. 12 likes, 3 comments
2. [Bluesky] @user2: Taiwan Strait tensions rise as PLA conducts military exercises near median line. 45 likes, 8 comments
3. [CNN] "US announces new trade restrictions on Chinese tech companies" - Commerce Department unveiled sweeping export controls targeting advanced AI chips and semiconductor manufacturing equipment destined for China.
4. [ASPI] "Mapping China's influence operations in the Pacific" - New report reveals coordinated disinformation campaigns across Southeast Asian social media platforms.
5. [HRW] "China: New Arrests at Underground Protestant Churches" - Authorities have detained at least 15 members of underground house churches in multiple provinces.
6. [U.S. Treasury] "Treasury Sanctions Chinese Entities Supporting Russia's Military" - New sanctions target 5 Chinese companies providing dual-use technology to Russia.
7. [Bluesky] @user3: Hong Kong's new national security case against pro-democracy activists draws international condemnation. 67 likes, 15 comments
8. [White House] - No relevant China/Taiwan news in this period.
"""

    prompt = f"""你是一个专业的舆情分析师。请根据以下抓取数据，生成一份简报。

要求：
1. 标题：用一句话概括本时段舆情态势
2. 核心摘要：3-5句话总结最重要的事件和趋势
3. 分类统计：按主题分类（军事、贸易、人权、外交等）
4. 风险评级：低/中/高，并说明理由
5. 关注建议：下一步需要重点关注的方向

数据：
{sample_events}

请用中文输出，格式清晰。"""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        start = time.time()
        r = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, proxies=PROXIES, timeout=120)
        r.raise_for_status()
        data = r.json()
        elapsed = time.time() - start
        content = data.get("message", {}).get("content", "")
        tokens = data.get("eval_count", 0)
        print(f"✅ 简报生成成功 ({elapsed:.1f}s, {tokens} tokens):")
        print("-" * 50)
        print(content)
        print("-" * 50)
        return True
    except Exception as e:
        print(f"❌ 摘要生成失败: {e}")
        return False


def main():
    print(f"🔍 Ollama 服务地址: {OLLAMA_BASE}")
    print()

    # 测试1: 连通性
    model = test_connectivity()
    if not model:
        print("\n⛔ 服务不可用，请先启动 Ollama 并确保模型已加载")
        sys.exit(1)

    # 测试2: 单轮对话
    if not test_single_chat(model):
        print("\n⛔ 单轮对话测试失败")
        sys.exit(1)

    # 测试3: 舆情摘要
    if not test_summary(model):
        print("\n⛔ 舆情摘要测试失败")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("🎉 全部测试通过！模型可用于舆情分析")
    print("=" * 50)


if __name__ == "__main__":
    main()
