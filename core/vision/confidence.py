"""
视觉识别置信度评估（三信号）
1. 双采样 Jaccard：对同一图像两次独立识别，结果重合度（0~1）
2. 清晰度：图像边缘能量（拉普拉斯方差近似），过低说明模糊/黑屏
3. 任务一致性：识别结果是否覆盖任务要求的关键实体

综合 → high / medium / low
"""
import re

HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.45
MIN_CLARITY = 40.0  # 拉普拉斯方差下限


def _char_bigrams(text: str) -> set:
    """字符级 bigram 集合（中文/英文统一处理，比整词对中文更鲁棒）"""
    s = re.sub(r"\s+", "", str(text or "").lower())
    if not s:
        return set()
    if len(s) == 1:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """两段识别文本的 Jaccard 重合度（基于字符 bigram 集合）"""
    ta, tb = _char_bigrams(text_a), _char_bigrams(text_b)
    if not ta and not tb:
        return 1.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def clarity_score(image_path: str) -> float:
    """
    图像清晰度：拉普拉斯方差近似
    需要 PIL + numpy；不可用时返回 100（不惩罚）
    """
    try:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as im:
            img = im.convert("L")
        arr = np.asarray(img, dtype=np.float64)
        lap = np.gradient(arr)[0] + np.gradient(arr)[1]
        return float(np.var(lap))
    except Exception:
        return 100.0


def _key_terms(text: str) -> list:
    """
    提取任务关键实体：
    - 英文单词整体作为一个 term
    - 中文连续串拆成 2-gram 集合（无分词器下的鲁棒近似）
    """
    terms = []
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]+", str(text or "")):
        tok = m.group(0)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,}", tok):
            terms.append(tok.lower())
        elif len(tok) > 1:
            terms.extend(tok[i:i + 2] for i in range(len(tok) - 1))
        else:
            terms.append(tok)
    return terms


def task_coverage(result_text: str, task_prompt: str) -> float:
    """
    任务一致性：识别结果覆盖任务关键实体的比例
    英文按词、中文按 2-gram，统计在结果中的命中率
    """
    key_terms = _key_terms(task_prompt)
    if not key_terms:
        return 1.0
    res_lower = str(result_text or "").lower().replace(" ", "")
    hit = 0
    for t in key_terms:
        if t.lower() in res_lower:
            hit += 1
    return hit / len(key_terms)


def evaluate_confidence(result_text: str, task_prompt: str = "",
                        second_sample: str = None, image_path: str = None) -> dict:
    """
    综合三信号评估置信度
    :param result_text: VL 识别结果
    :param second_sample: 第二次独立采样结果（可选；缺省时 jaccard 信号取 0.5 中性值）
    :param image_path: 图像路径（可选；缺省时不惩罚清晰度）
    :return: {level, score, signals: {jaccard, clarity, coverage}, reason}
    """
    if second_sample:
        j = jaccard_similarity(result_text, second_sample)
    else:
        j = 0.5  # 中性值：不惩罚也不加分
    cl = clarity_score(image_path) if image_path else 100.0
    cov = task_coverage(result_text, task_prompt)

    # 三信号加权
    clarity_norm = min(1.0, cl / 100.0) if cl > 0 else 0.0
    score = 0.4 * j + 0.3 * clarity_norm + 0.3 * cov

    reasons = []
    if j < MEDIUM_THRESHOLD:
        reasons.append("双采样重合度低")
    if cl < MIN_CLARITY:
        reasons.append("图像模糊或过暗")
    if cov < MEDIUM_THRESHOLD:
        reasons.append("结果未覆盖任务关键实体")

    if score >= HIGH_THRESHOLD and not reasons:
        level = "high"
    elif score >= MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    return {
        "level": level,
        "score": round(score, 3),
        "signals": {"jaccard": round(j, 3), "clarity": round(cl, 1),
                    "coverage": round(cov, 3)},
        "reason": "；".join(reasons) if reasons else "三信号均达标",
    }


def route_by_confidence(level: str) -> str:
    """按置信度路由处理方式"""
    if level == "high":
        return "可写入沙盘并进入核心记忆"
    if level == "medium":
        return "写入沙盘并标记 [视觉读图·待核验]"
    return "丢弃并重试（低置信度）"
