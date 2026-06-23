"""
进程清理模块 - 确保爬虫的子进程（Chrome/Xvfb）被完全清理
"""
import os
import signal
import sys
import atexit
import subprocess

# 子进程组ID（由 crawl 函数设置）
_child_pgid = None


def set_child_pgid():
    """设置子进程组ID，用于后续清理"""
    global _child_pgid
    _child_pgid = os.getpgid(0)


def cleanup_child_processes():
    """清理所有子进程（Chrome、Xvfb等）"""
    global _child_pgid
    if _child_pgid is None:
        return
    
    try:
        # 先发送 SIGTERM，给进程时间清理
        os.killpg(_child_pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    
    # 等1秒，然后强制杀
    import time
    time.sleep(1)
    
    try:
        os.killpg(_child_pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def kill_orphaned_processes():
    """清理残留的 Chrome/Xvfb 进程"""
    patterns = [
        ("chrome-headless-shell", "chromium"),
        ("chromedriver", "chromedriver"),
        ("Xvfb", "Xvfb"),
    ]
    
    for keyword, display_name in patterns:
        try:
            result = subprocess.run(
                ["pgrep", "-f", keyword],
                capture_output=True, text=True, timeout=5
            )
            pids = result.stdout.strip().split('\n')
            pids = [p for p in pids if p]
            if pids:
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                print(f"  [清理] 杀掉 {len(pids)} 个残留 {display_name} 进程")
        except Exception:
            pass
    
    # 清理 Xvfb lock 文件
    import glob
    for lock in glob.glob("/tmp/.X*-lock"):
        try:
            os.remove(lock)
            print(f"  [清理] 删除残留锁文件 {lock}")
        except Exception:
            pass
    for sock in glob.glob("/tmp/.X11-unix/X*"):
        try:
            os.remove(sock)
        except Exception:
            pass


# 注册退出时清理
atexit.register(cleanup_child_processes)
