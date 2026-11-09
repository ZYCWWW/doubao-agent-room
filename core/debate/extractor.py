# core/debate/extractor.py
"""
共识提取器：LLM 从全部发言提取三清单（结构化 JSON，走 json_extract 鲁棒解析）
- consensus：各方一致结论（可入记忆 is_verified）
- disagreements：分歧点（只写沙盘）
- open_questions：开放问题（只写沙盘；猜想库已于 2026-11-08 移除）
"""
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.model_router import router
from core.json_extract import extract_json


def _build_prompt(topic: str, turns) -> str:
    lines = "\n".join(f"[{t.role}] {t.content}" for t in turns)
    return f"""辩论主题：{topic}
发言记录：
{lines}

请提取三份清单，只输出 JSON：
{{
  "consensus": ["各方一致同意的结论1", "..."],
  "disagreements": ["仍存在分歧的点1", "..."],
  "open_questions": ["值得进一步求证的问题1", "..."]
}}
若某项为空，给空数组。"""


def extract(debate: object) -> dict:
    """提取共识/分歧/开放问题；失败时降级为启发式结果"""
    try:
        resp = ask(
            [{"role": "user", "content": _build_prompt(debate.topic, debate.turns)}],
            temperature=0.1, model=router.route("summary"),
        )
        obj = extract_json(resp.content) if resp.success else None
    except Exception:
        obj = None
    if not isinstance(obj, dict):
        obj = {}
    consensus = obj.get("consensus") or []
    disagreements = obj.get("disagreements") or []
    open_questions = obj.get("open_questions") or []
    # 启发式兜底：无共识文本时取最后一轮 synthesizer 发言
    if not consensus:
        for t in reversed(debate.turns):
            if t.role == "synthesizer":
                consensus = [t.content[:300]]
                break
    if not disagreements:
        for t in debate.turns:
            if t.role == "critic":
                disagreements.append(t.content[:200])
    debate.consensus = "\n".join(consensus) if consensus else None
    debate.disagreements = disagreements
    debate.open_questions = open_questions
    return {"consensus": consensus, "disagreements": disagreements,
            "open_questions": open_questions}
