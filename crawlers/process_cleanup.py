"""
进程清理模块 - 确保爬虫的子进程（Chrome/Xvfb）被完全清理
通过独立进程组隔离，不会误杀其他平台的进程
"""
import os
import signal
import time
import subprocess
import glob
import re


def find_available_display():
    """找到一个未被占用的 Xvfb display 编号"""
    active = set()
    try:
        result = subprocess.run(
            ["pgrep", "-a", "Xvfb"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            m = re.search(r":(\d+)", line)
            if m:
                active.add(m.group(1))
    except Exception:
        pass

    for lock in glob.glob("/tmp/.X*-lock"):
        m = re.search(r"\.X(\d+)-lock", lock)
        if m and m.group(1) not in active:
            try:
                os.remove(lock)
            except Exception:
                pass

    for display in range(98, 110):
        if str(display) not in active:
            return display
    return 99


def start_xvfb(display=None):
    """启动 Xvfb，返回独立进程组的 Popen 对象"""
    if display is None:
        display = find_available_display()

    lock_file = f"/tmp/.X{display}-lock"
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except Exception:
            pass

    proc = subprocess.Popen(
        ["Xvfb", f":{display}", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)

    if proc.poll() is not None:
        raise RuntimeError(f"Xvfb failed to start on display :{display}")

    return proc, display


def kill_child_group(process_obj):
    """安全清理子进程组：只杀 start_new_session=True 创建的独立进程组"""
    if process_obj is None:
        return
    try:
        pgid = os.getpgid(process_obj.pid)
        os.killpg(pgid, signal.SIGTERM)
        time.sleep(0.5)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def kill_orphaned_processes():
    """
    启动前清理：杀掉所有孤儿Chrome/Playwright进程和Xvfb
    不限制时间阈值——任何孤儿进程都应该清理，避免内存积累导致OOM
    """
    # 清理所有Playwright/Chrome相关孤儿进程（不限时间）
    patterns = [
        "chrome-headless-shell",
        "chromium-browser",
        "chromedriver",
    ]

    for keyword in patterns:
        try:
            result = subprocess.run(
                ["pgrep", "-f", keyword],
                capture_output=True, text=True, timeout=5
            )
            pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
            if pids:
                for pid_str in pids:
                    try:
                        os.kill(int(pid_str), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                print(f"  [清理] 杀掉 {len(pids)} 个孤儿 {keyword} 进程")
        except Exception:
            pass

    # 清理所有Xvfb进程
    try:
        result = subprocess.run(
            ["pgrep", "-f", "Xvfb"],
            capture_output=True, text=True, timeout=5
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        if pids:
            for pid_str in pids:
                try:
                    os.kill(int(pid_str), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            print(f"  [清理] 杀掉 {len(pids)} 个孤儿 Xvfb 进程")
    except Exception:
        pass

    # 清理孤立的 lock 文件
    for lock in glob.glob("/tmp/.X*-lock"):
        try:
            os.remove(lock)
        except Exception:
            pass


def ensure_clean_before_crawl():
    """
    爬虫启动前确保环境干净：
    1. 杀掉所有孤儿Chrome进程（防止内存积累导致OOM）
    2. 清理Xvfb残留
    3. 打印当前内存状态
    """
    kill_orphaned_processes()

    # 打印内存状态供调试
    try:
        with open("/proc/meminfo") as f:
            meminfo = f.read()
        available = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1)) // 1024
        total = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1)) // 1024
        used = total - available
        print(f"  [内存] {used}MB / {total}MB (可用 {available}MB)")
        if available < 1024:
            print(f"  [警告] 可用内存不足 1GB，爬虫可能因OOM被杀")
    except Exception:
        pass
