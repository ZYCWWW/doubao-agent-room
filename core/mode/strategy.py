# core/mode/strategy.py
"""
策略学习器：任务 → 模式预测
- 样本 < 3：默认 steady
- 特征匹配加权：危险/探索/科研/猜想关键词 → inquiry
- 用户手动覆盖 = 强标签
- 反馈更新样本分数（STRATEGY_DECAY 跟随偏好）
"""
import time
import threading
from core.mode.models import ModeSample, MODES

INQUIRY_KEYWORDS = ("猜想", "科研", "探索", "求证", "研究", "数形结合",
                    "假设", "因果", "推理", "设计", "头脑风暴", "新想法", "发明")
STEADY_KEYWORDS = ("执行", "操作", "点击", "打开", "发送", "输入", "保存", "删除",
                   "整理", "备份", "文件", "复制", "移动")

STRATEGY_DECAY = 0.3   # 反馈衰减系数


class ModeStrategy:
    def __init__(self):
        self._samples: list[ModeSample] = []
        self._lock = threading.RLock()
        self._manual_mode = None       # 用户手动指定，覆盖一切

    def set_manual_mode(self, mode: str) -> bool:
        if mode not in MODES:
            return False
        self._manual_mode = mode
        return True

    def clear_manual_mode(self) -> None:
        self._manual_mode = None

    def predict_mode(self, task: str) -> str:
        if self._manual_mode:
            return self._manual_mode
        # 关键词是很强的信号，优先于样本投票：任务明说"求证/猜想/研究"就该走 inquiry。
        # 旧版只看样本比例，样本<3 一律 steady —— 新环境永远进不了求证模式。
        hint = keyword_hint(task)
        if hint:
            return hint
        with self._lock:
            samples = [s for s in self._samples if s.feedback_score >= 0.5]
        if len(samples) < 3:
            return "steady"
        # 加权特征匹配：样本中 inquiry 高分样本越多 → inquiry
        total = sum(s.feedback_score for s in samples)
        inq_w = sum(s.feedback_score for s in samples if s.mode == "inquiry")
        return "inquiry" if (inq_w / total if total else 0.5) >= 0.5 else "steady"

    def record_choice(self, task: str, mode: str, score: float = 1.0) -> None:
        """用户手动覆盖 = 强标签（score=1.0）"""
        with self._lock:
            self._samples.append(ModeSample(task_features=task[:100], mode=mode,
                                           feedback_score=min(1.0, max(0.0, score)),
                                           ts=time.time()))
            # 样本上限 200，淘汰最旧
            if len(self._samples) > 200:
                self._samples = self._samples[-200:]

    def feedback(self, mode: str, score: float) -> None:
        """反馈更新：给同模式样本加权衰减"""
        with self._lock:
            for s in self._samples:
                if s.mode == mode:
                    s.feedback_score = min(1.0, s.feedback_score * (1 - STRATEGY_DECAY)
                                           + score * STRATEGY_DECAY)

    def status(self) -> dict:
        with self._lock:
            return {"manual_mode": self._manual_mode,
                    "predict": self.predict_mode(""),
                    "samples": len(self._samples),
                    "steady": sum(1 for s in self._samples if s.mode == "steady"),
                    "inquiry": sum(1 for s in self._samples if s.mode == "inquiry")}


mode_strategy = ModeStrategy()


def keyword_hint(task: str) -> str:
    """关键词快速预判（供 predict 参考与面板展示）"""
    if any(k in task for k in INQUIRY_KEYWORDS):
        return "inquiry"
    if any(k in task for k in STEADY_KEYWORDS):
        return "steady"
    return ""
