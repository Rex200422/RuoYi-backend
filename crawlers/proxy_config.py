"""
统一代理配置模块
所有爬虫从这里读取代理设置。
"""
from crawler_config import PROXIES, PLAYWRIGHT_PROXY


def get_proxies():
    """获取代理配置（字典）"""
    return PROXIES


def get_playwright_proxy():
    """获取 Playwright 代理配置"""
    return PLAYWRIGHT_PROXY
