"""
TF-RI 记忆打分与衰减
score = importance + log2(access_count+1)*0.5 - decay_rate*days
permanent 记忆不衰减（is_permanent=True）
"""
import math
import time

DEFAULT_DECAY_RATE = 0.02
ACCESS_GAIN = 0.5  # 每次访问的 log2 增益系数


def tfri_score(importance: float,
               access_count: int = 0,
               created_ts: float = None,
               decay_rate: float = DEFAULT_DECAY_RATE,
               permanent: bool = False) -> float:
    """计算一条记忆当前的 TF-RI 分数"""
    created_ts = created_ts if created_ts is not None else time.time()
    access = max(0, int(access_count or 0))
    base = float(importance) + math.log2(access + 1) * ACCESS_GAIN
    if permanent:
        return base
    days = max(0.0, (time.time() - created_ts) / 86400.0)
    return max(0.0, base - decay_rate * days)


def update_access_metadata(meta: dict) -> dict:
    """访问计数 +1（返回更新后的 metadata 副本，调用方负责写回）"""
    m = dict(meta or {})
    try:
        m["access_count"] = int(m.get("access_count", 0)) + 1
    except (TypeError, ValueError):
        m["access_count"] = 1
    m["last_access_ts"] = time.time()
    return m
