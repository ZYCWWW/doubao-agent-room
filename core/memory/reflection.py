"""
反思记忆生成
任务结束后异步生成失败教训/成功经验，写入 reflection 分区
轻量结构化组装；可选 LLM 精炼（默认不做，避免阻塞）
"""
import time
from core.logger import root_logger

REFLECTION_COLL = "reflection"


class Reflection:
    def __init__(self, topic: str, scenario: str, lesson: str, source: str,
                 kind: str = "lesson"):
        self.topic = topic
        self.scenario = scenario
        self.lesson = lesson
        self.source = source
        self.kind = kind  # lesson | experience
        self.created_ts = time.time()

    def to_text(self) -> str:
        return (f"【反思·{self.kind}】主题：{self.topic}\n"
                f"场景：{self.scenario}\n"
                f"经验/教训：{self.lesson}\n"
                f"来源：{self.source}")

    def to_metadata(self) -> dict:
        return {
            "type": "reflection",
            "kind": self.kind,
            "topic": self.topic,
            "source": self.source,
            "created_ts": self.created_ts,
            "base_score": 7.0,      # 反思默认较高重要性
            "access_count": 0,
            "is_permanent": False,
        }


def build_reflection(topic: str, scenario: str, lesson: str, source: str,
                     kind: str = "lesson") -> Reflection:
    """构造反思对象（不落库；由 memory_service.add_reflection 落库）"""
    if not topic or not lesson:
        root_logger.warning("反思主题或内容为空，跳过")
        return None
    return Reflection(topic, scenario, lesson, source, kind)
