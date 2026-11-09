# core/debate/scheduler.py
"""
辩论调度状态机：
running → turn(角色发言) → validator 校验 → 分歧标记
running → converged（consensus_score ≥ 0.75）
running → max_rounds（轮次达上限）
→ extracted（提取共识/分歧/开放问题）
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_ROLES = [
    {"name": "proposer", "quota": 3, "prompt_hint": "提出并捍卫一个主张，给出理由与证据"},
    {"name": "critic", "quota": 3, "prompt_hint": "质疑主张：找反例、漏洞、边界条件"},
    {"name": "constraint_checker", "quota": 3, "prompt_hint": "检查主张是否符合已知事实与约束"},
    {"name": "synthesizer", "quota": 3, "prompt_hint": "综合各方观点，收敛出可接受结论或明确指出分歧"},
]

CONVERGE_THRESHOLD = 0.75
MAX_ROUNDS = 8


@dataclass
class DebateTurn:
    role: str
    content: str
    ts: float = field(default_factory=time.time)
    tags: list = field(default_factory=list)   # 分歧/共识/开放问题


@dataclass
class DebateSession:
    id: str
    topic: str
    roles: list = field(default_factory=lambda: [dict(r) for r in DEFAULT_ROLES])
    turns: list = field(default_factory=list)
    status: str = "running"                    # running / converged / max_rounds / aborted
    consensus_score: float = 0.0
    consensus: Optional[str] = None
    disagreements: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)


_sessions: dict[str, DebateSession] = {}


def start_debate(topic: str, roles: Optional[list] = None) -> str:
    """新建辩论会话，返回 debate_id"""
    did = uuid.uuid4().hex[:8]
    _sessions[did] = DebateSession(
        id=did, topic=topic,
        roles=[dict(r) for r in (roles or DEFAULT_ROLES)],
    )
    return did


def add_turn(debate_id: str, role: str, content: str, tags: Optional[list] = None) -> bool:
    s = _sessions.get(debate_id)
    if not s or s.status not in ("running",):
        return False
    s.turns.append(DebateTurn(role=role, content=content, tags=list(tags or [])))
    return True


def get_session(debate_id: str) -> Optional[DebateSession]:
    return _sessions.get(debate_id)


def list_sessions(status: Optional[str] = None) -> list[dict]:
    out = []
    for s in _sessions.values():
        if status and s.status != status:
            continue
        out.append({"id": s.id, "topic": s.topic, "status": s.status,
                    "turns": len(s.turns), "consensus_score": round(s.consensus_score, 3)})
    return out


def set_status(debate_id: str, status: str) -> bool:
    s = _sessions.get(debate_id)
    if not s:
        return False
    s.status = status
    return True


def get_round(debate_id: str) -> int:
    s = _sessions.get(debate_id)
    return len(s.turns) // max(1, len(s.roles)) if s else 0
