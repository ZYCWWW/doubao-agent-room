# core/mode/models.py
"""双模式数据模型"""
import time
from dataclasses import dataclass

MODES = ("steady", "inquiry")


@dataclass
class ModeSample:
    task_features: str
    mode: str                       # steady / inquiry
    feedback_score: float = 0.5
    ts: float = 0.0
