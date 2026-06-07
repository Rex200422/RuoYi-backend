"""
RuoYi 舆情爬虫 - 统一代理配置模块
=====================================

功能概述：
    所有爬虫从这里读取代理设置，确保代理配置集中管理。

代理设置来源：
    代理地址定义在 crawler_config.py 的 PROXY 变量中，
    本模块将其转换为两种格式供不同场景使用：
      - requests 库使用的字典格式 (PROXIES)
      - Playwright 浏览器使用的字典格式 (PLAYWRIGHT_PROXY)

使用示例:
    from proxy_config import PROXIES, get_playwright_proxy

    # requests 库使用
    requests.get(url, proxies=PROXIES)

    # Playwright 使用
    browser = p.chromium.launch(proxy=get_playwright_proxy())
"""
from crawler_config import PROXIES, PLAYWRIGHT_PROXY


def get_proxies():
    """
    获取 requests 库使用的代理配置字典。

    返回值:
        dict: 格式为 {"http": "http://...", "https": "http://..."}
              可直接传给 requests.get(..., proxies=...) 参数
    """
    return PROXIES


def get_playwright_proxy():
    """
    获取 Playwright 浏览器使用的代理配置。

    返回值:
        dict: 格式为 {"server": "http://..."}
              可直接传给 browser.launch(proxy=...) 参数
    """
    return PLAYWRIGHT_PROXY
