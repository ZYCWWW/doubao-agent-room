# plugins/installed/toolchain_plugin/module.py
"""工具链宏插件（2 工具，**无面板**）

原来属于内置插件 `tools/toolchain_tool.py`（run_toolchain / toolchain_list）。
两个函数体逐字照搬，没有改行为；业务本体在 `core/toolchain.py`，一行未动。

**为什么不做面板**：工具链是「模型侧省 ReAct 多轮规划」的能力，没有对应的整页面板
（面板页 11 的三个 tab 是运行模式 / 工具耗时 / 模型闲聊，与工具链无关）。
给它新造一个页面等于新增功能，超出「把已有能力外置成插件」的范围。
所以按 `calc_plugin` 的先例做成纯工具插件：manifest 不写 `panel`，面板侧栏不会多出页。

当前内置链两条（`core/toolchain.py` 的 `BUILTIN_CHAINS`）：
- `web_research`：browser_fetch → llm_discuss（抓页面再让模型总结要点）
- `software_learn`：software_learn_and_execute（启动软件并自学其帮助文档）
`hypothesis_verify` 随猜想库于 2026-11-08 移除。
"""
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
