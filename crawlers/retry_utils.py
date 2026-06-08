"""
重试工具模块
============

提供通用的请求重试机制，应对代理冷启动或网络抖动导致的临时失败。

用法:
    from retry_utils import with_retry

    # 基本用法
    result = with_retry(lambda: session.get(url, timeout=15), description="获取列表页")

    # Playwright 页面加载（失败时自动关闭并重建页面）
    with_retry(lambda: page.goto(url, timeout=60000), description="访问列表页")

    # 自定义重试次数
    result = with_retry(fn, max_retries=5, retry_delay=(3, 8))
"""

import time
import random


def with_retry(fn, max_retries=3, retry_delay=(2, 5), description="请求"):
    """
    带重试的函数执行器。

    参数:
        fn:           要执行的函数（无参数的 lambda 或 callable）
        max_retries:  最大重试次数（默认3次，含首次）
        retry_delay:  重试间隔范围（秒），随机取值 (min, max)
        description:  描述信息，用于日志输出

    返回值:
        fn() 的返回值

    异常:
        如果所有重试都失败，抛出最后一次的异常
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = random.uniform(*retry_delay)
                print(f"  ⚠️ {description} 失败(第{attempt + 1}次): {str(e)[:80]}")
                print(f"     {delay:.1f}秒后重试...")
                time.sleep(delay)
            else:
                print(f"  ❌ {description} 重试{max_retries}次均失败")
                raise last_error


def with_retry_goto(page, url, goto_kwargs=None, max_retries=3, retry_delay=(2, 5), description="页面加载"):
    """
    Playwright 页面加载专用重试。
    如果 page.goto 失败，自动刷新页面后重试。

    参数:
        page:          Playwright Page 对象
        url:           目标 URL
        goto_kwargs:   page.goto() 的额外参数（如 timeout, wait_until）
        max_retries:   最大重试次数
        retry_delay:   重试间隔范围（秒）
        description:   描述信息
    """
    if goto_kwargs is None:
        goto_kwargs = {}

    last_error = None
    for attempt in range(max_retries):
        try:
            page.goto(url, **goto_kwargs)
            return  # 成功则返回
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = random.uniform(*retry_delay)
                print(f"  ⚠️ {description} 失败(第{attempt + 1}次): {str(e)[:80]}")
                print(f"     {delay:.1f}秒后重试...")
                time.sleep(delay)
                # 刷新页面以清理可能的损坏状态
                try:
                    page.goto("about:blank")
                except Exception:
                    pass
            else:
                print(f"  ❌ {description} 重试{max_retries}次均失败")
                raise last_error
