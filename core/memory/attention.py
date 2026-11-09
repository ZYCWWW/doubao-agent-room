"""
自适应注意力：检索结果重排序 + 预算分片
rank = 0.5*sim + 0.3*tfri + 0.2*freshness + type_gain
预算分片：core 50% / cold 30% / archive 不默认注入
"""
import time

# 记忆类型增益（反思/规则优先级更高）
TYPE_GAIN = {
    "reflection": 0.15,
    "rule": 0.10,
    "fact": 0.05,
    "skill": 0.05,
    "episode": 0.0,
}

# 默认分片比例
CORE_RATIO = 0.5
COLD_RATIO = 0.3
ARCHIVE_RATIO = 0.0  # archive 不默认注入


def normalize_sim(distance: float) -> float:
    """chroma 距离转相似度（0~1，越小越相似）"""
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, 1.0 - d / 2.0))


def freshness(created_ts: float, half_life_days: float = 30.0) -> float:
    """新鲜度：按半衰期衰减的 0~1 值"""
    try:
        created = float(created_ts)
    except (TypeError, ValueError):
        return 0.0
    days = max(0.0, (time.time() - created) / 86400.0)
    return 2.0 ** (-days / half_life_days)


def rank(item: dict) -> float:
    """
    对一条检索结果计算注意力排名
    item 至少含：distance / metadata(base_score, access_count, created_ts, type, is_permanent)
    """
    meta = item.get("metadata") or {}
    sim = normalize_sim(item.get("distance"))
    try:
        importance = float(meta.get("base_score", meta.get("importance", 5.0)))
    except (TypeError, ValueError):
        importance = 5.0
    try:
        access = int(meta.get("access_count", 0))
    except (TypeError, ValueError):
        access = 0
    try:
        created = float(meta.get("created_ts", meta.get("last_access_ts", time.time())))
    except (TypeError, ValueError):
        created = time.time()
    mtype = str(meta.get("type", "episode"))
    gain = TYPE_GAIN.get(mtype, 0.0)
    if meta.get("is_permanent") is True:
        gain += 0.05

    import math
    tfri = float(importance) + math.log2(access + 1) * 0.5
    return 0.5 * sim + 0.3 * min(1.0, tfri / 10.0) + 0.2 * freshness(created) + gain


def rerank(results: list, top_k: int = None) -> list:
    """按 rank 降序重排检索结果"""
    scored = sorted((rank(it), it) for it in results)
    scored.reverse()
    out = [it for _, it in scored]
    if top_k is not None:
        out = out[:top_k]
    return out


def partition_budget(budget: int,
                     core_ratio: float = CORE_RATIO,
                     cold_ratio: float = COLD_RATIO) -> dict:
    """把总预算切分为各分区预算（archive 为 0 即不注入）"""
    budget = max(1, int(budget))
    core_n = int(budget * core_ratio)
    cold_n = int(budget * cold_ratio)
    # 余量优先补 core
    core_n += budget - core_n - cold_n
    return {
        "core": max(1, core_n),
        "cold": max(0, cold_n),
        "archive": 0,
    }
