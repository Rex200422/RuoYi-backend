"""
统一代理配置模块
所有爬虫从这里读取代理设置，更换代理时只需修改此文件。

链式代理架构：
  本地 → HTTP代理(192.168.0.14:7890) → SOCKS5代理(zkapeteraaa@203.166.136.112:443) → 目标网站

需要先启动 gost 链式代理中继：
  gost -L=socks5://0.0.0.0:1080 -F=http://192.168.0.14:7890 -F=socks5://zkapeteraaa:zkapeteraaa@203.166.136.112:443
"""

import os
import subprocess
import atexit

# ============================================================
# 代理地址配置（只需改这里）
# ============================================================

# 第一层：HTTP 代理
LAYER1_PROXY = "http://192.168.0.14:7890/"

# 第二层：SOCKS5 代理
LAYER2_PROXY = "socks5://zkapeteraaa:zkapeteraaa@203.166.136.112:443"

# gost 本地中继端口（链式代理监听地址）
GOST_LOCAL_PORT = 1080

# ============================================================
# 自动生成的代理地址
# ============================================================

# 链式代理地址（通过 gost 本地中继）
CHAIN_PROXY = f"socks5://127.0.0.1:{GOST_LOCAL_PORT}"

# requests/urllib 使用的 proxies 字典（走链式代理）
PROXIES = {
    "http": CHAIN_PROXY,
    "https": CHAIN_PROXY,
}

# 单层代理（仅第一层，备用）
SINGLE_PROXY = LAYER1_PROXY

SINGLE_PROXIES = {
    "http": SINGLE_PROXY,
    "https": SINGLE_PROXY,
}

# Playwright proxy 配置
PLAYWRIGHT_PROXY = {"server": CHAIN_PROXY}


def ensure_gost_running():
    """确保 gost 链式代理中继正在运行，如果没有则自动启动。"""
    import socket
    # 检查本地 1080 端口是否已有服务
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("127.0.0.1", GOST_LOCAL_PORT))
        s.close()
        return True  # 已经在运行
    except (ConnectionRefusedError, socket.timeout, OSError):
        pass

    # 查找 gost 可执行文件
    gost_bin = None
    for path in ["/usr/local/bin/gost", "/usr/bin/gost"]:
        if os.path.isfile(path):
            gost_bin = path
            break
    if not gost_bin:
        print("[proxy_config] 警告：gost 未安装，链式代理不可用，将回退到单层代理")
        return False

    # 启动 gost（后台）
    cmd = [
        gost_bin,
        "-L", f"socks5://0.0.0.0:{GOST_LOCAL_PORT}",
        "-F", LAYER1_PROXY,
        "-F", LAYER2_PROXY,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待启动
    import time
    for _ in range(10):
        time.sleep(0.5)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", GOST_LOCAL_PORT))
            s.close()
            print(f"[proxy_config] gost 链式代理已启动，监听 127.0.0.1:{GOST_LOCAL_PORT}")
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            continue

    print("[proxy_config] 警告：gost 启动超时，将回退到单层代理")
    return False


def get_proxies(use_chain=True):
    """
    获取代理配置。
    use_chain=True:  返回链式代理（本地→HTTP代理→SOCKS5代理→目标）
    use_chain=False: 返回单层代理（仅HTTP代理）
    """
    if use_chain and ensure_gost_running():
        return PROXIES
    return SINGLE_PROXIES


def get_playwright_proxy(use_chain=True):
    """获取 Playwright 代理配置。"""
    if use_chain and ensure_gost_running():
        return PLAYWRIGHT_PROXY
    return {"server": SINGLE_PROXY}
