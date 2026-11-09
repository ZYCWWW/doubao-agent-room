# core/debate/consensus.py
"""
收敛判定：0.4*观点重合度 + 0.4*LLM一致性 + 0.2*(1-反驳率)
- 观点重合：各角色发言文本两两字符 bigram Jaccard（复用 vision.confidence）
- 反驳率：critic 发言含否定/反驳词的占比（持续下降=收敛）
"""
import re
from core.vision.confidence import jaccard_similarity
from core.debate.scheduler import CONVERGE_THRESHOLD, MAX_ROUNDS

_REBUTTAL_WORDS = ("不同意", "反驳", "错误", "漏洞", "反例", "不对", "但是", "然而",
                   "缺陷", "质疑", "边界", "例外", "风险", "失败")


def _overlap_score(texts: list[str]) -> float:
    """各发言两两 Jaccard 平均重合度（0~1）"""
    if len(texts) < 2:
        return 0.0
    total, cnt = 0.0, 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            total += jaccard_similarity(texts[i], texts[j])
            cnt += 1
    return total / cnt if cnt else 0.0


def _rebuttal_rate(critic_texts: list[str]) -> float:
    if not critic_texts:
        return 0.0
    hits = sum(1 for t in critic_texts if any(w in t for w in _REBUTTAL_WORDS))
    return hits / len(critic_texts)


def _consistency_score(turns, topic: str) -> float:
    """
    一致性：最近若干轮是否在收窄（不做真实 LLM 调用时的启发式近似）
    - 最近半轮（后半轮）发言与前半轮的重合度上升 → 一致性高
    """
    texts = [t.content for t in turns]
    if len(texts) < 2:
        return 0.5
    half = len(texts) // 2
    first_half = texts[:half]
    second_half = texts[half:]
    if not first_half or not second_half:
        return 0.5
    # 后半轮内部重合度 - 前半轮内部重合度（收窄为正）
    s1 = _overlap_score(first_half)
    s2 = _overlap_score(second_half)
    # 归一化到 0~1
    return max(0.0, min(1.0, 0.5 + (s2 - s1)))


def consensus_score(turns, topic: str) -> float:
    """计算收敛分数（0~1），0.75 视为收敛"""
    texts = [t.content for t in turns]
    overlap = _overlap_score(texts)
    consistency = _consistency_score(turns, topic)
    critic_texts = [t.content for t in turns if t.role == "critic"]
    rebuttal = _rebuttal_rate(critic_texts)
    score = 0.4 * overlap + 0.4 * consistency + 0.2 * (1.0 - rebuttal)
    # 反驳完全消失（且 critic 有 ≥2 轮发言机会）= 强收敛信号
    if rebuttal == 0.0 and len(critic_texts) >= 2:
        score = min(1.0, score + 0.2)
    return score


def check_converged(debate: object) -> bool:
    """更新并判断是否收敛"""
    score = consensus_score(debate.turns, debate.topic)
    debate.consensus_score = score
    if score >= CONVERGE_THRESHOLD:
        debate.status = "converged"
        return True
    return False


def is_max_rounds(debate: object) -> bool:
    rounds = len(debate.turns) // max(1, len(debate.roles))
    return rounds >= MAX_ROUNDS
