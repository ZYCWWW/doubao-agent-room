# core/events.py
"""
进程内事件总线：子系统解耦 + 面板实时观测
- publish 同步分发到订阅者（异常不阻断）
- 环形快照缓冲，供 dashboard 轮询"同步变化更新"
- 所有主题见 TOPICS
"""
import time
import threading
from collections import defaultdict, deque
from typing import Callable, Any, Optional

# 预定义主题（约定优于配置；自定义主题也允许）
TOPICS = {
    "memory", "sandbox", "tool", "approval",
    "debate", "session", "metrics",
}


class EventBus:
    """进程内单例事件总线"""

    def __init__(self, max_snapshot: int = 2000):
        self._lock = threading.RLock()
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._snapshot: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_snapshot)
        )

    # ---- 订阅 / 发布 ----
    def subscribe(self, topic: str, handler: Callable[[str, Any], None]) -> None:
        with self._lock:
            self._handlers[topic].append(handler)

    def publish(self, topic: str, payload: Any) -> None:
        ev = (time.time(), payload)
        with self._lock:
            self._snapshot[topic].append(ev)
            handlers = list(self._handlers.get(topic, ()))
        for h in handlers:
            try:
                h(topic, payload)
            except Exception:
                # 订阅者异常不阻断主流程（总线只做通知，不做业务）
                pass

    # ---- 快照查询（面板轮询） ----
    def snapshot(self, topic: Optional[str] = None,
                 since_ts: float = 0.0, limit: int = 200) -> list:
        """返回 (ts, payload) 列表，按时间升序；topic=None 返回全部主题"""
        with self._lock:
            topics = [topic] if topic else list(self._snapshot.keys())
            out = []
            for t in topics:
                for ts, payload in self._snapshot.get(t, ()):
                    if ts >= since_ts:
                        out.append((t, ts, payload))
            out.sort(key=lambda x: x[1])
            return out[-limit:] if limit > 0 else out

    def latest(self, topic: str) -> Optional[Any]:
        """最新一条 payload"""
        with self._lock:
            buf = self._snapshot.get(topic)
            if not buf:
                return None
            return buf[-1][1]

    def clear(self, topic: Optional[str] = None) -> None:
        with self._lock:
            if topic:
                self._snapshot[topic].clear()
            else:
                self._snapshot.clear()

    # ---- 状态 ----
    def topic_count(self, topic: str) -> int:
        with self._lock:
            return len(self._snapshot.get(topic, ()))


# 全局单例
bus = EventBus()
