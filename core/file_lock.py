# core/file_lock.py
"""
统一文件锁：进程内线程互斥 + 跨进程独占锁（Windows 无 fcntl，用锁文件 O_CREAT|O_EXCL）
- 所有沙盘/md/记忆/配置文件的写操作都应走 file_lock
- 带超时与自动清理（锁文件留下 stale 时按内容时间戳判断）
"""
import os
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# 进程内锁（路径 -> RLock）
_thread_locks: dict[str, threading.RLock] = {}
_thread_guard = threading.Lock()
# 线程级重入计数（跨进程锁文件不可重入，需按线程记录持有深度）
_local = threading.local()

_LOCK_SUFFIX = ".dlock"


class FileLockTimeout(TimeoutError):
    pass


def _thread_lock(path: str) -> threading.RLock:
    with _thread_guard:
        lock = _thread_locks.get(path)
        if lock is None:
            lock = threading.RLock()
            _thread_locks[path] = lock
        return lock


def _lock_file_path(target: str) -> Path:
    p = Path(target)
    parent = p.parent if p.suffix else p
    parent.mkdir(parents=True, exist_ok=True)
    return parent / (p.name + _LOCK_SUFFIX)


def _acquire_cross_process(lock_file: Path, timeout: float, poll: float) -> bool:
    """独占创建锁文件；锁文件内写时间戳，用于 stale 判断"""
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(time.time()))
            return True
        except FileExistsError:
            # stale 检测：锁文件存在但超过 10 分钟未更新（进程崩溃残留）
            try:
                age = time.time() - lock_file.stat().st_mtime
                if age > 600:
                    try:
                        lock_file.unlink()
                    except OSError:
                        pass
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(poll)
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(poll)


@contextmanager
def file_lock(target: str, timeout: float = 30.0, poll: float = 0.05):
    """
    统一写锁入口：
    with file_lock("sandbox/xxx.md"):
        ... 写操作 ...
    超时抛 FileLockTimeout。
    支持：进程内线程互斥（RLock）+ 跨进程独占（锁文件）+ 同线程重入。
    """
    tlock = _thread_lock(target)
    if not tlock.acquire(timeout=timeout):
        raise FileLockTimeout(f"线程锁超时: {target}")
    lock_file = _lock_file_path(target)
    lf_key = str(lock_file)

    held = getattr(_local, "held_locks", None)
    if held is None:
        held = _local.held_locks = {}
    depth = held.get(lf_key, 0)

    acquired = False
    try:
        if depth > 0:
            # 同线程已持有：重入，只加深计数
            held[lf_key] = depth + 1
            yield
            return
        acquired = _acquire_cross_process(lock_file, timeout, poll)
        if not acquired:
            raise FileLockTimeout(f"跨进程锁超时: {lock_file}")
        held[lf_key] = 1
        yield
    finally:
        if lf_key in held:
            held[lf_key] -= 1
            if held[lf_key] <= 0:
                del held[lf_key]
                if acquired:
                    try:
                        lock_file.unlink()
                    except OSError:
                        pass
        tlock.release()


def locked_write_text(target: str, content: str, encoding: str = "utf-8",
                      timeout: float = 30.0) -> None:
    """便捷：加锁写文本文件"""
    with file_lock(target, timeout=timeout):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(content, encoding=encoding)


def locked_read_text(target: str, encoding: str = "utf-8",
                     default: str = "") -> str:
    """便捷：读文本文件（读无需跨进程锁，仅进程内防并发写撕裂）"""
    p = Path(target)
    if not p.exists():
        return default
    with _thread_lock(target):
        try:
            return p.read_text(encoding=encoding)
        except OSError:
            return default
