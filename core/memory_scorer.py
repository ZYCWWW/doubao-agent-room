"""
记忆打分与衰减算法
情景记忆会随时间自动降分，低于阈值自动归档
"""
import time
from core.config_loader import config_loader

# 读取配置阈值
SCORE_SAVE = config_loader["memory"]["score_threshold_save"]
SCORE_ARCHIVE = config_loader["memory"]["score_threshold_archive"]
DECAY = config_loader["memory"]["score_decay_rate"]

def calculate_score(base_score: float, created_ts: float) -> float:
    """
    计算当前记忆分数
    base_score：初始打分（LLM评估）
    created_ts：创建时间戳（秒）
    衰减按天计算：score_decay_rate=0.02 表示每天衰减0.02分
    """
    delta_days = (time.time() - created_ts) / 86400.0
    new_score = base_score - DECAY * delta_days
    return max(new_score, 0.0)

def need_archive(score: float) -> bool:
    """判断是否满足归档条件"""
    return score < SCORE_ARCHIVE
