# services/debate_service.py
"""
辩论服务门面：封装 core/debate 状态机 + 收敛判定 + 共识提取
供 debate_start / debate_speak / debate_extract 工具与 main 使用
"""
from core.debate import (start_debate, add_turn, get_session, list_sessions,
                         check_converged, is_max_rounds, extract,
                         DEFAULT_ROLES, CONVERGE_THRESHOLD, MAX_ROUNDS)


def debate_start(topic: str, roles: list = None, session_id: str = "") -> dict:
    did = start_debate(topic, roles)
    return {"debate_id": did, "topic": topic, "status": "running",
            "max_rounds": MAX_ROUNDS, "converge_threshold": CONVERGE_THRESHOLD}


def debate_speak(debate_id: str, role: str, content: str) -> dict:
    s = get_session(debate_id)
    if not s:
        return {"ok": False, "error": "辩论不存在"}
    if s.status != "running":
        return {"ok": False, "error": f"辩论已结束（{s.status}）"}
    if not add_turn(debate_id, role, content):
        return {"ok": False, "error": "发言失败"}
    converged = check_converged(s)
    if not converged and is_max_rounds(s):
        s.status = "max_rounds"
    return {"ok": True, "debate_id": debate_id, "round": len(s.turns),
            "consensus_score": round(s.consensus_score, 3),
            "status": s.status, "converged": converged}


def debate_extract(debate_id: str, session_id: str = "") -> dict:
    s = get_session(debate_id)
    if not s:
        return {"ok": False, "error": "辩论不存在"}
    if s.status == "running":
        # 未收敛也可强制提取当前观点
        pass
    result = extract(s)
    return {"ok": True, "debate_id": debate_id, "status": s.status,
            "consensus": result["consensus"],
            "disagreements": result["disagreements"],
            "open_questions": result["open_questions"]}


def debate_list(status: str = "") -> list:
    return list_sessions(status or None)


def debate_roles() -> list:
    return [r["name"] for r in DEFAULT_ROLES]
