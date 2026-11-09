# tools/tutor_tool.py
"""Tutor 教学工具：tutor_ask / tutor_teach"""
from services.tutor_service import tutor_service


def _tutor_ask(question: str, session_id: str = ""):
    return tutor_service.ask(question, session_id)


def _tutor_teach(topic: str, session_id: str = ""):
    return tutor_service.teach(topic, session_id)


plugin_export = {
    "tutor_ask": {"desc": "Tutor 教学问答：基于知识库技能分步讲解（question）", "func": _tutor_ask},
    "tutor_teach": {"desc": "生成主题教学大纲（topic）", "func": _tutor_teach},
}
