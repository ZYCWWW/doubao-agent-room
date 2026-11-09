# core/async_pool.py
"""
后台任务池：一次性任务 + 周期任务
- 慢操作（记忆衰减写回/GC/摘要/沙盘分析）全部后台化
- 周期任务异常不终止调度
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Optional
from core.logger import root_logger


def _safe_run(fn: Callable, name: str) -> None:
    try:
        fn()
    except Exception as e:
        root_logger.error(f"后台任务[{name}]异常: {e}")


class AsyncPool:
    def __init__(self, max_workers: int = 4):
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="agent-async"
        )
        self._periodic: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # ---- 一次性任务 ----
    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    # ---- 周期任务 ----
    def schedule_periodic(self, fn: Callable, interval_sec: float,
                          name: str, run_immediately: bool = False) -> threading.Thread:
        with self._lock:
            existing = self._periodic.get(name)
            if existing is not None and existing.is_alive():
                return existing  # 同名任务已存在：幂等，不重复启动

        def _loop():
            if run_immediately:
                _safe_run(fn, name)
            while not self._stop.wait(interval_sec):
                _safe_run(fn, name)

        t = threading.Thread(target=_loop, daemon=True, name=f"periodic-{name}")
        with self._lock:
            self._periodic[name] = t
        t.start()
        return t

    def stop_periodic(self, name: str) -> None:
        with self._lock:
            self._periodic.pop(name, None)

    # ---- 关闭 ----
    def shutdown(self, wait: bool = False) -> None:
        self._stop.set()
        self._pool.shutdown(wait=wait)


# 全局单例
async_pool = AsyncPool()
