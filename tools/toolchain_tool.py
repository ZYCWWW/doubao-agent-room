# tools/toolchain_tool.py
"""
工具链宏工具：run_toolchain / toolchain_list

已迁移：2026-11-09 起不再内置注册，改由功能插件
`plugins/installed/toolchain_plugin/module.py` 导出（两个函数体逐字照搬）。
本文件保留为回滚参考，main.py 不再 import 它。
业务本体 `core/toolchain.py` 未改动。
"""
import json
from core.toolchain import run_toolchain, list_chains


def _run_toolchain(name: str, variables: dict = None, session_id: str = ""):
    r = run_toolchain(name, variables or {}, session_id)
    parts = [f"工具链「{r.name}」执行{'成功' if r.ok else '失败'}", ""]
    for i, step in enumerate(r.step_results):
        parts.append(f"步骤{i+1}: {str(step)[:600]}")
    if r.error:
        parts.append(f"错误：{r.error}")
    return "\n".join(parts)


def _toolchain_list(session_id: str = ""):
    chains = list_chains()
    return "\n".join(f"- {c['name']}: {c['desc']} → {' → '.join(c['steps'])}" for c in chains)


plugin_export = {
    "run_toolchain": {"desc": "一键执行内置工具链（web_research/software_learn），variables 传参数如 url/query/software", "func": _run_toolchain},
    "toolchain_list": {"desc": "列出可用工具链及步骤", "func": _toolchain_list},
}
