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
    """安全清理子进程组"""
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
    """启动前清理：只杀运行超过30分钟的残留进程"""
    kill_threshold = 1800
    patterns = [
        ("chrome-headless-shell", "chromium"),
        ("chromedriver", "chromedriver"),
    ]

    for keyword, display_name in patterns:
        try:
            result = subprocess.run(
                ["pgrep", "-f", keyword],
                capture_output=True, text=True, timeout=5
            )
            pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
            killed = 0
            for pid_str in pids:
                try:
                    stat = subprocess.run(
                        ["ps", "-o", "etimes=", "-p", pid_str],
                        capture_output=True, text=True, timeout=5
                    )
                    elapsed = int(stat.stdout.strip())
                    if elapsed > kill_threshold:
                        os.kill(int(pid_str), signal.SIGKILL)
                        killed += 1
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            if killed > 0:
                print(f"  [清理] 杀掉 {killed} 个残留 {display_name} 进程（运行超过{kill_threshold//60}分钟）")
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["pgrep", "-a", "Xvfb"], capture_output=True, text=True, timeout=5
        )
        active_displays = set()
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            m = re.search(r":(\d+)", line)
            if m:
                active_displays.add(m.group(1))
        for lock in glob.glob("/tmp/.X*-lock"):
            m = re.search(r"\.X(\d+)-lock", lock)
            if m and m.group(1) not in active_displays:
                try:
                    os.remove(lock)
                    print(f"  [清理] 删除孤立锁文件 {lock}")
                except Exception:
                    pass
    except Exception:
        pass
