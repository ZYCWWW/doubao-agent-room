# core/metrics.py
"""
性能埋点：按阶段记录耗时，供监控面板与审计
- record(stage, dur_ms)：手动记录
- timer(stage)：上下文管理器自动计时
- summary()：聚合（均值/p50/p99/次数），面板展示
- flush()：把当日累计落盘到 logs/metrics-YYYYMMDD-<pid>.json
- aggregate()：把多个进程、多天的落盘文件合并成与 summary() 同形状的结果

持久化默认关闭（Metrics() 无副作用，测试可随便构造）；
只有全局单例 metrics 打开持久化，因此面板/主进程重启后「今日」「累计」都还在。
按 pid 分文件写，避免面板进程与主进程互相覆盖同一份统计。
"""
import glob
import json
import os
import statistics
import threading
import time
from collections import defaultdict, deque
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_dir() -> str:
    """优先用 config 里配的日志目录；取不到就退回项目 logs/"""
    try:
        from core.config_loader import config_loader
        d = (config_loader["log"]["log_path"] or "").strip()
        if d:
            return d
    except Exception:
        pass
    return os.path.join(_ROOT, "logs")


def _today_tag() -> str:
    return time.strftime("%Y%m%d")


class Metrics:
    def __init__(self, keep: int = 2000, persist: bool = False,
                 persist_dir: Optional[str] = None, sample_cap: int = 400,
                 flush_every: float = 5.0):
        self._lock = threading.RLock()
        self._samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=keep))
        self._counters: dict[str, int] = defaultdict(int)
        # 当日精确累计（样本被 deque 截断后，均值仍按真实次数算）
        self._total: dict[str, int] = defaultdict(int)
        self._sum: dict[str, float] = defaultdict(float)
        self._keep = keep
        self._persist = persist
        self._dir_override = persist_dir
        self._sample_cap = sample_cap
        self._flush_every = flush_every
        self._day = _today_tag()
        self._loaded = False
        self._dirty = False
        self._last_flush = 0.0

    # ---- 目录与文件 ----
    def _dir(self) -> str:
        return self._dir_override or _default_dir()

    def _file_for(self, tag: str) -> str:
        return os.path.join(self._dir(), f"metrics-{tag}-{os.getpid()}.json")

    # ---- 落盘 ----
    def _before_write(self) -> None:
        """写记录前统一处理：跨日换文件、首次从磁盘补当日数据。调用方须持锁。"""
        if not self._persist:
            return
        tag = _today_tag()
        if tag != self._day:
            self._flush_locked()          # 把昨天写完再翻页
            self._samples.clear()
            self._total.clear()
            self._sum.clear()
            self._day = tag
            self._loaded = False
        if not self._loaded:
            self._load_locked()

    def _load_locked(self) -> None:
        self._loaded = True
        p = self._file_for(self._day)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        for st, v in (data.get("stages") or {}).items():
            self._total[st] = int(v.get("count") or 0)
            self._sum[st] = float(v.get("sum_ms") or 0.0)
            for x in (v.get("samples") or [])[-self._keep:]:
                try:
                    self._samples[st].append(float(x))
                except Exception:
                    pass
        for k, n in (data.get("counters") or {}).items():
            try:
                self._counters[k] = int(n)
            except Exception:
                pass

    def _payload_locked(self) -> dict:
        stages = {}
        for st in list(self._samples.keys()):
            s = tuple(self._samples[st])
            if not s:
                continue
            ordered = sorted(s)
            n = len(ordered)
            total = int(self._total.get(st, n)) or n
            total_ms = float(self._sum.get(st, sum(ordered)))
            stages[st] = {
                "count": total,
                "sum_ms": round(total_ms, 2),
                "avg_ms": round(total_ms / max(1, total), 2),
                "p50_ms": round(statistics.median(ordered), 2),
                "p99_ms": round(ordered[min(n - 1, int(n * 0.99))], 2),
                "samples": [round(float(x), 1) for x in ordered[-self._sample_cap:]],
            }
        return {
            "day": self._day,
            "pid": os.getpid(),
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stages": stages,
            "counters": dict(self._counters),
        }

    def flush(self) -> bool:
        if not self._persist:
            return False
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> bool:
        if not self._persist or not self._dirty:
            return False
        d = self._dir()
        try:
            os.makedirs(d, exist_ok=True)
            p = self._file_for(self._day)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._payload_locked(), f, ensure_ascii=False)
            os.replace(tmp, p)
            self._last_flush = time.time()
            self._dirty = False
            return True
        except Exception:
            return False

    # ---- 记录 ----
    def record(self, stage: str, dur_ms: float) -> None:
        with self._lock:
            self._before_write()
            self._samples[stage].append(dur_ms)
            self._total[stage] += 1
            self._sum[stage] += dur_ms
            self._dirty = True
            due = time.time() - self._last_flush >= self._flush_every
        if due:
            self.flush()

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._before_write()
            self._counters[name] += n
            self._dirty = True
            due = time.time() - self._last_flush >= self._flush_every
        if due:
            self.flush()

    # ---- 聚合 ----
    def _snapshot(self, stage: str) -> tuple:
        with self._lock:
            self._before_write()     # 只读接口也要先补一次当日落盘数据，否则重启后 summary() 是空的
            return tuple(self._samples.get(stage, ()))

    def avg(self, stage: str) -> Optional[float]:
        s = self._snapshot(stage)
        return statistics.mean(s) if s else None

    def p50(self, stage: str) -> Optional[float]:
        s = self._snapshot(stage)
        return statistics.median(s) if s else None

    def p99(self, stage: str) -> Optional[float]:
        s = self._snapshot(stage)
        if not s:
            return None
        ordered = sorted(s)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]

    def count_of(self, name: str) -> int:
        with self._lock:
            self._before_write()
            return self._counters.get(name, 0)

    def summary(self) -> dict:
        """返回 {stages: {stage: {count, avg_ms, p50_ms, p99_ms}}, counters}"""
        with self._lock:
            self._before_write()
            stages = list(self._samples.keys())
        out = {}
        for st in stages:
            s = self._snapshot(st)
            if not s:
                continue
            ordered = sorted(s)
            out[st] = {
                "count": len(s),
                "avg_ms": round(statistics.mean(s), 2),
                "p50_ms": round(statistics.median(s), 2),
                "p99_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))], 2),
            }
        counters = dict(self._counters)
        return {"stages": out, "counters": counters}

    def reset(self, stage: Optional[str] = None) -> None:
        with self._lock:
            if stage:
                self._samples[stage].clear()
                self._total[stage] = 0
                self._sum[stage] = 0.0
            else:
                self._samples.clear()
                self._counters.clear()
                self._total.clear()
                self._sum.clear()
            self._dirty = True
        self.flush()


def aggregate(directory: str = "", pattern: str = "metrics-*.json",
              sample_cap: int = 800) -> dict:
    """把落盘文件合并成与 summary() 同形状的结果。

    pattern="metrics-<今日YYYYMMDD>-*.json" 得到「今日」；"metrics-*.json" 得到「累计」。
    """
    d = directory or _default_dir()
    files = sorted(glob.glob(os.path.join(d, pattern)))
    stages: dict[str, dict] = {}
    counters: dict[str, int] = defaultdict(int)
    used = 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        used += 1
        for st, v in (data.get("stages") or {}).items():
            rec = stages.setdefault(st, {"count": 0, "sum_ms": 0.0, "samples": []})
            rec["count"] += int(v.get("count") or 0)
            rec["sum_ms"] += float(v.get("sum_ms") or 0.0)
            sm = v.get("samples") or []
            if sm:
                rec["samples"].extend(float(x) for x in sm)
                if len(rec["samples"]) > sample_cap:
                    rec["samples"] = rec["samples"][-sample_cap:]
        for k, n in (data.get("counters") or {}).items():
            try:
                counters[k] += int(n)
            except Exception:
                pass

    out = {}
    for st, rec in stages.items():
        n = max(1, int(rec["count"]))
        sm = sorted(rec["samples"])
        item = {
            "count": int(rec["count"]),
            "avg_ms": round(rec["sum_ms"] / n, 2),
            "p50_ms": round(statistics.median(sm), 2) if sm else 0.0,
            "p99_ms": round(sm[min(len(sm) - 1, int(len(sm) * 0.99))], 2) if sm else 0.0,
        }
        out[st] = item
    return {"stages": out, "counters": dict(counters),
            "files": used, "dir": d, "pattern": pattern}


def today_pattern() -> str:
    return f"metrics-{_today_tag()}-*.json"


# 全局单例：打开持久化（面板/主进程共用同一套日志目录，按 pid 分文件）
metrics = Metrics(persist=True)


class timer:
    """用法：with timer("llm_chat"): ...  ；可选传入 Metrics 实例（默认全局 metrics）"""

    def __init__(self, stage: str, m: "Metrics" = None):
        self.stage = stage
        self._m = m or metrics
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        dur_ms = (time.perf_counter() - self._start) * 1000.0
        self._m.record(self.stage, dur_ms)
        return False
