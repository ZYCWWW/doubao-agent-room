# core/session_manager.py
"""
会话生命周期：注册/心跳/闲置清理
- register + heartbeat：维护活跃时间
- cleanup_idle：闲置超时自动清理（沙盘目录 + 会话注册）
- active_sessions：面板状态判断
"""
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from core.config_loader import config_loader
from core.events import bus
from core.file_lock import file_lock
from core.logger import audit, root_logger

# 会话沙盘根（与 imaginary_service 同源：config sandbox.root_path）
SANDBOX_ROOT = Path(config_loader["sandbox"]["root_path"])


class SessionManager:
    def __init__(self, idle_timeout: float = 1800.0):
        self._sessions: dict[str, float] = {}   # sid -> last_active_ts
        self._lock = threading.RLock()
        self._idle_timeout = max(0.05, idle_timeout)

    # ---- 注册 / 心跳 ----
    def register(self, sid: str) -> None:
        now = time.time()
        with self._lock:
            self._sessions[sid] = now
        bus.publish("session", {"op": "register", "sid": sid, "ts": now})

    def heartbeat(self, sid: str) -> None:
        with self._lock:
            if sid in self._sessions:
                self._sessions[sid] = time.time()

    def unregister(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)
        bus.publish("session", {"op": "unregister", "sid": sid})

    # ---- 状态 ----
    def is_active(self, sid: str) -> bool:
        with self._lock:
            ts = self._sessions.get(sid)
            if ts is None:
                return False
            return (time.time() - ts) < self._idle_timeout

    def active_sessions(self) -> list:
        """返回 [ {sid, last_active, idle_sec} ]，仅活跃会话"""
        now = time.time()
        with self._lock:
            return [
                {"sid": s, "last_active": t, "idle_sec": now - t}
                for s, t in self._sessions.items()
                if (now - t) < self._idle_timeout
            ]

    def last_active(self, sid: str) -> Optional[float]:
        with self._lock:
            return self._sessions.get(sid)

    # ---- 闲置清理 ----
    def cleanup_idle(self, timeout: Optional[float] = None) -> list:
        """清理闲置超时会话，返回被清理的 sid 列表"""
        timeout = timeout or self._idle_timeout
        now = time.time()
        victims = []
        with self._lock:
            for sid, ts in list(self._sessions.items()):
                if (now - ts) >= timeout:
                    victims.append((sid, now - ts))
            for sid, _idle in victims:
                del self._sessions[sid]
        for sid, idle_sec in victims:
            self._cleanup_session_data(sid)
            audit(f"SESSION_IDLE_CLEANUP | sid={sid} | idle={idle_sec:.0f}s")
        if victims:
            bus.publish("session", {"op": "cleanup", "sids": [v[0] for v in victims]})
        return [v[0] for v in victims]

    def _cleanup_session_data(self, sid: str) -> None:
        """删除会话沙盘目录（含索引/节点/研究）"""
        session_dir = SANDBOX_ROOT / sid
        if not session_dir.exists():
            return
        try:
            with file_lock(str(session_dir / ".cleanup"), timeout=10.0):
                shutil.rmtree(str(session_dir), ignore_errors=True)
            root_logger.info(f"已清理闲置会话沙盘: {session_dir}")
        except Exception as e:
            root_logger.error(f"清理会话沙盘失败 {session_dir}: {e}")

    # ---- 生命周期 ----
    def shutdown(self) -> None:
        with self._lock:
            self._sessions.clear()


# 全局单例（闲置超时读配置，默认 30min）
_session_cfg = config_loader.get("session") or {}
session_manager = SessionManager(
    idle_timeout=float(_session_cfg.get("idle_timeout", 1800))
)
