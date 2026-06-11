"""
爬虫任务日志工具
================

每个爬虫任务（一次crawl()调用）生成一个独立日志文件。
日志包含时间戳、事件处理详情、请求信息、错误堆栈。

日志路径：crawlers/logs/{site}_{config_id}_{timestamp}.log
自动清理：保留最近 200 个日志文件。

用法（在爬虫 main() 入口处）：
    from crawl_logger import TaskLog

    with TaskLog("Guardian", config_id=11, log_id=4900) as log:
        log.info("列表页获取 20 篇文章")
        log.request("GET", "https://example.com/news", status=200, ms=523)
        log.item(0, "收录", title="文章标题")
        log.item(1, "跳过", title="另一篇", reason="无关键词")
        log.item(2, "更新", title="第三篇")
        log.error(Exception("超时"), "获取文章详情")
        # 退出时自动写入汇总

查看日志：
    python3 crawl_logs.py          # 列出最近 50 条日志
    python3 crawl_logs.py --tail 5 # 列出最近 5 条
    python3 crawl_logs.py 1234     # 查看 log_id=1234 的日志内容
    python3 crawl_logs.py --clean  # 清理超过 7 天的日志
"""

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
MAX_LOG_FILES = 200  # 最多保留的日志文件数


class TaskLog:
    """
    单次爬取任务的日志记录器。

    输出到文件 + 控制台。文件格式：
        [2026-06-11 10:30:00.123] [Guardian#11] INFO  列表页获取 20 篇文章
        [2026-06-11 10:30:01.234] [Guardian#11] REQ   GET https://example.com → 200 (0.5s)
        [2026-06-11 10:30:01.567] [Guardian#11] ITEM  [0] 收录: 文章标题
        [2026-06-11 10:30:01.789] [Guardian#11] ERROR 获取文章详情: ConnectionTimeout
    """

    def __init__(self, site_name, config_id=None, log_id=None):
        """
        Args:
            site_name: 站点名称，如 "Guardian", "Bluesky"
            config_id: crawl_config.id
            log_id: crawl_log.id
        """
        self.site_name = site_name
        self.config_id = config_id or 0
        self.log_id = log_id or 0
        self.file = None
        self.log_path = None
        self.start_time = None
        self.stats = {"found": 0, "saved": 0, "updated": 0, "skipped": 0, "errors": 0}

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.error(exc_val, "未捕获异常")
        self.finish()

    def start(self):
        """创建日志文件并写入任务头部"""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_logs()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.site_name}_#{self.config_id}_{ts}.log"
        self.log_path = LOG_DIR / filename
        self.file = open(self.log_path, "w", encoding="utf-8")
        self.start_time = time.time()

        self.info(f"===== 任务开始 =====")
        self.info(f"站点: {self.site_name}  配置ID: {self.config_id}  日志ID: {self.log_id}")
        print(f"[LOG] 日志文件: {self.log_path}", file=sys.stderr)

    def info(self, msg):
        """普通信息"""
        self._write("INFO", msg)

    def item(self, idx, action, title="", reason="", extra=""):
        """记录单条事件处理"""
        self.stats[action] = self.stats.get(action, 0) + 1
        detail = f"[{idx}] {action}"
        if title:
            detail += f": {title[:80]}"
        if reason:
            detail += f" ({reason})"
        if extra:
            detail += f" {extra}"
        self._write("ITEM", detail)

    def request(self, method, url, status=None, ms=None):
        """记录HTTP请求"""
        detail = f"{method} {url[:120]}"
        if status is not None:
            detail += f" → {status}"
        if ms is not None:
            detail += f" ({ms:.0f}ms)"
        self._write("REQ", detail)

    def error(self, exc, context=""):
        """记录异常"""
        self.stats["errors"] += 1
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        self._write("ERR", f"{context}: {exc}")
        for line in tb[-3:]:
            self._write("ERR", f"  {line.strip()}")

    def finish(self):
        """结束任务，写入汇总"""
        elapsed = time.time() - self.start_time if self.start_time else 0
        self.info(f"===== 任务结束 =====")
        self.info(f"耗时: {elapsed:.1f}s  发现:{self.stats['found']}  保存:{self.stats['saved']}  更新:{self.stats['updated']}  跳过:{self.stats['skipped']}  错误:{self.stats['errors']}")
        if self.file:
            self.file.close()
            self.file = None

    def _write(self, level, msg):
        """写入日志行（同时输出到控制台）"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        tag = f"{self.site_name}#{self.config_id}"
        line = f"[{ts}] [{tag}] {level:5s} {msg}"

        # 写入文件
        if self.file:
            self.file.write(line + "\n")
            self.file.flush()

        # 同时输出到控制台（带颜色）
        if level == "ERROR":
            print(f"\033[31m{line}\033[0m", file=sys.stderr)
        elif level == "ITEM":
            print(line, file=sys.stderr)
        else:
            print(line, file=sys.stderr)

    def _cleanup_old_logs(self):
        """清理旧日志，保留最近 MAX_LOG_FILES 个"""
        if not LOG_DIR.exists():
            return
        files = sorted(LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime)
        if len(files) > MAX_LOG_FILES:
            for f in files[: len(files) - MAX_LOG_FILES]:
                f.unlink()


def view_log(log_path):
    """查看日志文件内容"""
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    print(content)


def list_logs(tail=50):
    """列出最近的日志文件"""
    if not LOG_DIR.exists():
        print("日志目录不存在")
        return
    files = sorted(LOG_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:tail]
    if not files:
        print("没有日志文件")
        return
    print(f"{'文件名':<50} {'大小':>8} {'修改时间':<20}")
    print("-" * 80)
    for f in files:
        size = f.stat().st_size
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{f.name:<50} {size:>7}B {mtime:<20}")


def clean_old_logs(days=7):
    """清理超过指定天数的日志"""
    if not LOG_DIR.exists():
        print("日志目录不存在")
        return
    cutoff = time.time() - days * 86400
    deleted = 0
    for f in LOG_DIR.glob("*.log"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    print(f"已清理 {deleted} 个超过 {days} 天的日志文件")


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="爬虫日志查看工具")
    parser.add_argument("log_id", nargs="?", type=int, help="查看指定 log_id 的日志")
    parser.add_argument("--tail", type=int, default=50, help="列出最近N个日志（默认50）")
    parser.add_argument("--clean", action="store_true", help="清理超过7天的旧日志")
    parser.add_argument("--days", type=int, default=7, help="清理天数阈值")
    parser.add_argument("--all", action="store_true", help="列出所有日志文件")
    args = parser.parse_args()

    if args.log_id:
        # 查找包含 log_id 的文件
        if LOG_DIR.exists():
            files = list(LOG_DIR.glob(f"*_{args.log_id}_*.log"))
            if files:
                view_log(files[-1])
            else:
                print(f"未找到 log_id={args.log_id} 的日志")
        else:
            print("日志目录不存在")
    elif args.clean:
        clean_old_logs(args.days)
    else:
        list_logs(args.all and 999999 or args.tail)
