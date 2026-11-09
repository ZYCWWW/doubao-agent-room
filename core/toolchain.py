# core/toolchain.py
"""
工具链宏：预定义工具序列一键执行，省 ReAct 多轮规划
- 内置链：web_research / software_learn
  （hypothesis_verify 随猜想库于 2026-11-08 移除）
- 步骤间引用：{var} 来自调用方 variables；{last_result} = 上一步输出
"""
import json
from dataclasses import dataclass, field
from typing import Any, Optional

BUILTIN_CHAINS: dict[str, dict] = {
    "web_research": {
        "desc": "网页调研：抓取页面 → 让大模型总结要点",
        "steps": [
            {"name": "browser_fetch", "params": {"url": "{url}", "query": "{query}"}},
            {"name": "llm_discuss", "params": {"question": "请总结以下资料的核心要点：\n{last_result}"}},
        ],
    },
    "software_learn": {
        "desc": "软件自学：启动软件并让 Agent 自学其帮助文档",
        "steps": [
            {"name": "software_learn_and_execute", "params": {"software": "{software}"}},
        ],
    },
}


@dataclass
class ToolStep:
    name: str
    params: dict = field(default_factory=dict)


@dataclass
class Toolchain:
    name: str
    desc: str
    steps: list = field(default_factory=list)


@dataclass
class ChainResult:
    ok: bool
    name: str
    step_results: list = field(default_factory=list)
    error: str = ""


def list_chains() -> list[dict]:
    return [{"name": k, "desc": v["desc"], "steps": [s["name"] for s in v["steps"]]}
            for k, v in BUILTIN_CHAINS.items()]


def _render(template: Any, variables: dict) -> Any:
    """把参数模板中的 {var} 替换为变量值（dict/list 递归处理）"""
    if isinstance(template, str):
        try:
            return template.format(**variables)
        except (KeyError, IndexError, ValueError):
            return template
    if isinstance(template, dict):
        return {k: _render(v, variables) for k, v in template.items()}
    if isinstance(template, list):
        return [_render(v, variables) for v in template]
    return template


def run_toolchain(name: str, variables: dict, session_id: str,
                  tool_map: Optional[dict] = None) -> ChainResult:
    """执行内置工具链（tool_map 缺省时运行时从 main 取，避免循环导入）"""
    chain = BUILTIN_CHAINS.get(name)
    if not chain:
        return ChainResult(ok=False, name=name, error=f"未知工具链：{name}")
    if tool_map is None:
        from main import ALL_TOOLS
        tool_map = ALL_TOOLS
    steps = chain["steps"]
    vars_ = dict(variables or {})
    results = []
    for i, step in enumerate(steps):
        try:
            tool = tool_map.get(step["name"])
            if not tool:
                results.append(f"[步骤{i+1} 工具不存在] {step['name']}")
                return ChainResult(ok=False, name=name, step_results=results,
                                   error=f"工具不存在：{step['name']}")
            params = _render(step["params"], vars_)
            params.setdefault("session_id", session_id)
            out = tool["func"](**params)
            results.append(out)
            vars_["last_result"] = out
        except Exception as e:
            results.append(f"[步骤{i+1} 失败] {str(e)}")
            return ChainResult(ok=False, name=name, step_results=results, error=str(e))
    return ChainResult(ok=True, name=name, step_results=results)


def chains_desc() -> str:
    """系统 prompt 用：可用工具链清单（省 token）"""
    lines = ["可用工具链（一条命令完成多步）："]
    for name, c in BUILTIN_CHAINS.items():
        lines.append(f"- {name}: {c['desc']} 步骤: {' → '.join(s['name'] for s in c['steps'])}")
    return "\n".join(lines)
