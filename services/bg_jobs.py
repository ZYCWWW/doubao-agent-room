# services/bg_jobs.py
"""面板后台任务（R13）：把耗时 LLM 调用移出 Streamlit UI 线程。

只负责「起线程 + 记录状态」，不 import streamlit、不碰 st.*；
UI 侧轮询 snapshot()，用 st.fragment(run_every=...) 做进度显示。
"""
import threading
import time

_LOCK = threading.Lock()
_JOB = {"id": 0, "running": False, "kind": "", "started": 0.0, "result": "", "error": ""}


def start(kind: str, fn) -> bool:
    """起一个后台任务；已有任务在跑则返回 False（同一时间只跑一个，避免抢 Ollama）"""
    with _LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(id=_JOB["id"] + 1, running=True, kind=kind,
                    started=time.time(), result="", error="")
        job_id = _JOB["id"]

    def _worker():
        try:
            res = fn()
            with _LOCK:
                _JOB.update(running=False, result=str(res)[:300], error="")
        except Exception as e:  # 后台异常不能把面板带崩
            with _LOCK:
                _JOB.update(running=False, error=str(e)[:300])

    threading.Thread(target=_worker, daemon=True, name=f"bg-{kind}-{job_id}").start()
    return True


def snapshot() -> dict:
    with _LOCK:
        return dict(_JOB)


def busy() -> bool:
    with _LOCK:
        return bool(_JOB["running"])


def reset() -> None:
    """测试用：清空状态（不动正在跑的线程）"""
    with _LOCK:
        _JOB.update(id=0, running=False, kind="", started=0.0, result="", error="")
