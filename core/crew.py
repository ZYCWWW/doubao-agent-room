# core/crew.py
"""
子 Agent 协作层：主 Agent 可 spawn 轻量子 Agent（独立推理、共享沙盘会话）
- 异步执行（async_pool），受 llm_client 全局信号量约束（max=2）
- 结果写回沙盘节点，主 Agent 通过 collect_result 收编
- 角色：researcher / debate_host / vision_operator / tutor
"""
import time
import uuid
import threading
from dataclasses import dataclass, field
from typing import Optional
from core.async_pool import async_pool
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.model_router import router
from core.logger import root_logger

SUBAGENT_ROLES = ("researcher", "debate_host", "vision_operator", "tutor")


@dataclass
class SubAgentSpec:
    role: str                      # researcher / debate_host / vision_operator / tutor
    prompt: str                    # 子任务指令
    model: Optional[str] = None    # 指定模型，None → router 默认
    sandbox_node: str = ""         # 结果写回的沙盘节点名（如 debate_xxx/round_1.md）
    temperature: float = 0.3


@dataclass
class SubAgent:
    sub_id: str
    spec: SubAgentSpec
    status: str = "running"        # running / done / failed
    result: str = ""
    started_ts: float = field(default_factory=time.time)


_agents: dict[str, SubAgent] = {}
_lock = threading.RLock()


def _run(spec: SubAgentSpec, sub_id: str) -> None:
    """子 Agent 执行体：独立 LLM 推理 → 结果写回沙盘节点"""
    try:
        sys_prompt = f"你是 Agent 的子任务执行者，角色：{spec.role}。请专注完成子任务，输出简洁结论。"
        resp = ask(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": spec.prompt}],
            temperature=spec.temperature,
            model=spec.model or router.route("light"),
        )
        text = resp.content if resp.success else f"[子Agent失败] {resp.content}"
        with _lock:
            _agents[sub_id].result = text
            _agents[sub_id].status = "done" if resp.success else "failed"
        # 结果写回沙盘节点（失败也留痕）
        try:
            from services.imaginary_service import imaginary_service
            node_name = spec.sandbox_node or f"subagent_{sub_id}.md"
            imaginary_service.write_md("crew", node_name, text)
        except Exception as e:
            root_logger.warning(f"子Agent结果写沙盘失败: {e}")
    except Exception as e:
        with _lock:
            _agents[sub_id].status = "failed"
            _agents[sub_id].result = f"[子Agent异常] {str(e)}"
        root_logger.error(f"子Agent[{sub_id}]异常: {e}")


def spawn_subagent(spec: SubAgentSpec) -> str:
    """启动一个子 Agent，返回 sub_id（不阻塞）"""
    sub_id = uuid.uuid4().hex[:8]
    with _lock:
        _agents[sub_id] = SubAgent(sub_id=sub_id, spec=spec)
    async_pool.submit(_run, spec, sub_id)
    return sub_id


def collect_result(sub_id: str, timeout: float = 120.0) -> str:
    """等待并收集子 Agent 结果（阻塞至 timeout）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            a = _agents.get(sub_id)
        if a is None:
            return f"[子Agent不存在] {sub_id}"
        if a.status in ("done", "failed"):
            return a.result
        time.sleep(1.0)
    with _lock:
        a = _agents.get(sub_id)
    return a.result if a else f"[子Agent不存在] {sub_id}"


def get_status(sub_id: str) -> Optional[dict]:
    with _lock:
        a = _agents.get(sub_id)
    if a is None:
        return None
    return {"sub_id": a.sub_id, "role": a.spec.role, "status": a.status,
            "result": a.result[:200]}


def list_agents() -> list[dict]:
    with _lock:
        return [{"sub_id": a.sub_id, "role": a.spec.role, "status": a.status}
                for a in _agents.values()]


def cleanup_agents(older_than_sec: float = 3600.0) -> int:
    """清理超时子 Agent 记录（不影响运行中）"""
    now = time.time()
    dead = []
    with _lock:
        for sub_id, a in _agents.items():
            if a.status in ("done", "failed") and now - a.started_ts > older_than_sec:
                dead.append(sub_id)
        for sub_id in dead:
            del _agents[sub_id]
    return len(dead)
