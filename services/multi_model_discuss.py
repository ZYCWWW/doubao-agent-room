# services/multi_model_discuss.py
"""
多模型协同讨论：同一主题，不同角色由不同 Ollama 模型真实驱动发言
- proposer(提案/推理) → main: deepseekR1Q7B
- critic(批判/反例) → light: llama3.1:8b（格式快、批判犀利）
- constraint_checker(约束核验) → vision: qwen3.5:4b（轻量快速核验）
- synthesizer(收敛综合) → main: deepseekR1Q7B（推理综合强）
每轮四个角色依次发言，发言带上前序讨论上下文；收敛或达轮次上限停止。
"""
from core.debate import (start_debate, add_turn, get_session,
                         check_converged, is_max_rounds, extract,
                         DEFAULT_ROLES, MAX_ROUNDS)
from core.llm_client import llm_client
from services.llm_gateway import ask

DEFAULT_ROLE_MODELS = {
    "proposer": "deepseekR1Q7B:latest",
    "critic": "llama3.1:8b",
    "constraint_checker": "qwen3.5:4b",
    "synthesizer": "deepseekR1Q7B:latest",
}


def multi_model_discuss(topic: str, rounds: int = 2, role_models: dict = None,
                        timeout: float = 240.0) -> dict:
    """多个真实模型按角色协同讨论，返回讨论记录+共识/分歧/开放问题"""
    rm = {**DEFAULT_ROLE_MODELS, **(role_models or {})}
    did = start_debate(topic, roles=DEFAULT_ROLES)
    history = []
    max_rounds_eff = min(max(int(rounds), 1), MAX_ROUNDS)
    for r in range(max_rounds_eff):
        for role in DEFAULT_ROLES:
            name = role["name"]
            model = rm.get(name, "deepseekR1Q7B:latest")
            sys_prompt = (f"你是多模型协同讨论中的角色「{name}」。职责：{role['prompt_hint']}。"
                          f"请针对主题「{topic}」给出精简发言（150字内），只输出发言内容本身，不要任何格式前缀或角色名。")
            messages = [{"role": "system", "content": sys_prompt}]
            if history:
                messages.append({"role": "user",
                                 "content": "此前讨论记录（按发言顺序）：\n" + "\n".join(history[-8:])})
            else:
                messages.append({"role": "user", "content": topic})
            resp = ask(messages, temperature=0.5, timeout=timeout, model=model)
            if resp.success:
                content = resp.content.strip()[:600]
                add_turn(did, name, content)
                history.append(f"[{name}·{model.split(':')[0]}] {content}")
        s = get_session(did)
        if check_converged(s) or is_max_rounds(s):
            break
    s = get_session(did)
    try:
        res = extract(s)
    except Exception as e:  # 未收敛也可能提取，防御
        res = {"consensus": None, "disagreements": [], "open_questions": []}
    return {"debate_id": did, "status": s.status,
            "consensus_score": round(s.consensus_score, 3),
            "consensus": res["consensus"], "disagreements": res["disagreements"],
            "open_questions": res["open_questions"],
            "transcript": history}
