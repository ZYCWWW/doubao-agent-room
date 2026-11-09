"""
桌面动作审批队列（sqlite 持久化）
- 重启不丢：所有待审批/已执行记录写入 sandbox/.queue.db
- 会话隔离：按 sid 过滤，防跨会话误批
- 超时自动拒绝：pending 超过 timeout_sec 自动置为 rejected
- 兼容旧接口：GLOBAL_QUEUE 视图可同步
"""
import contextlib
import json
import os
import sqlite3
import time
import uuid
from core.config_loader import config_loader
from core.logger import root_logger

SANDBOX = config_loader["sandbox"]["root_path"]
DB_PATH = os.path.join(SANDBOX, ".queue.db")
DEFAULT_TIMEOUT_SEC = 1800.0  # 30 分钟


@contextlib.contextmanager
def _conn():
    """连接上下文：退出时提交并**关闭**。

    注意：sqlite3.Connection 自带的 __enter__/__exit__ 只提交/回滚事务，**不会关闭连接**，
    所以直接 `with sqlite3.connect(...) as conn:` 每次调用都漏一个句柄
    （表现为 ResourceWarning: unclosed database）。这里补上 close 才算干净。
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS desktop_queue (
            aid TEXT PRIMARY KEY,
            sid TEXT,
            action_type TEXT,
            payload TEXT,
            status TEXT,
            created_ts REAL,
            updated_ts REAL,
            timeout_ts REAL
        )
        """)
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class DesktopQueue:
    def enqueue(self, sid: str, action_type: str, payload: dict,
                timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> str:
        """入队，返回 aid"""
        aid = str(uuid.uuid4())
        now = time.time()
        with _conn() as conn:
            conn.execute(
                "INSERT INTO desktop_queue (aid, sid, action_type, payload, status, created_ts, updated_ts, timeout_ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, sid, action_type, json.dumps(payload, ensure_ascii=False),
                 "pending", now, now, now + max(0.05, float(timeout_sec))))
        root_logger.info(f"桌面队列入队 sid={sid} aid={aid} action={action_type}")
        return aid

    def list_queue(self, sid: str = None, status: str = None) -> list:
        """列出队列项；sid=None 全部会话，status=None 全部状态"""
        with _conn() as conn:
            sql = "SELECT aid, sid, action_type, payload, status, created_ts, updated_ts, timeout_ts FROM desktop_queue"
            conds, args = [], []
            if sid:
                conds.append("sid = ?")
                args.append(sid)
            if status:
                conds.append("status = ?")
                args.append(status)
            if conds:
                sql += " WHERE " + " AND ".join(conds)
            sql += " ORDER BY created_ts ASC"
            rows = conn.execute(sql, args).fetchall()
        out = []
        for aid, sid_, at, payload, st, cts, uts, tts in rows:
            out.append({
                "aid": aid, "sid": sid_, "action_type": at,
                "payload": json.loads(payload or "{}"), "status": st,
                "created_ts": cts, "updated_ts": uts, "timeout_ts": tts,
            })
        return out

    def get(self, aid: str) -> dict:
        """按 aid 取一项；不存在返回 None"""
        with _conn() as conn:
            row = conn.execute(
                "SELECT aid, sid, action_type, payload, status, created_ts, updated_ts, timeout_ts"
                " FROM desktop_queue WHERE aid = ?", (aid,)).fetchone()
        if not row:
            return None
        aid_, sid_, at, payload, st, cts, uts, tts = row
        return {"aid": aid_, "sid": sid_, "action_type": at,
                "payload": json.loads(payload or "{}"), "status": st,
                "created_ts": cts, "updated_ts": uts, "timeout_ts": tts}

    def update_status(self, aid: str, status: str) -> bool:
        """更新状态（exec/rejected/done）"""
        with _conn() as conn:
            cur = conn.execute(
                "UPDATE desktop_queue SET status = ?, updated_ts = ? WHERE aid = ?",
                (status, time.time(), aid))
            conn.commit()
            return cur.rowcount > 0

    def auto_reject_timeout(self, sid: str = None) -> int:
        """把超时的 pending 项自动置为 rejected，返回拒绝数量"""
        now = time.time()
        with _conn() as conn:
            if sid:
                cur = conn.execute(
                    "UPDATE desktop_queue SET status = 'rejected', updated_ts = ?"
                    " WHERE status = 'pending' AND timeout_ts < ? AND sid = ?",
                    (now, now, sid))
            else:
                cur = conn.execute(
                    "UPDATE desktop_queue SET status = 'rejected', updated_ts = ?"
                    " WHERE status = 'pending' AND timeout_ts < ?",
                    (now, now))
            conn.commit()
            n = cur.rowcount      # 连接关闭前取值
        if n:
            root_logger.info(f"桌面队列超时自动拒绝 {n} 项")
        return n

    def to_global_view(self) -> dict:
        """生成兼容旧 GLOBAL_QUEUE 的 {sid: [items]} 视图"""
        view = {}
        for it in self.list_queue():
            view.setdefault(it["sid"], []).append(it)
        return view


desktop_queue = DesktopQueue()
