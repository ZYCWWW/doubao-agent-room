# tools/research_tool.py
"""
⚠️ 2026-11-09 起此文件不再由 main.py 注册，科研七步已迁为功能插件：
    plugins/installed/research_plugin/（module.py 里同名 plugin_export + 面板页）
文件保留一版便于回滚，不要在这里继续加功能。

科研流水线工具（猜想库已于 2026-11-08 移除）：
research_create / research_step / research_status
"""
import json
from core.research import (create_research, get_research, research_step,
                          list_research, STEPS)

_STEP_NAMES = {k: v[0] for k, v in STEPS.items()}


def _research_create(research_topic: str, session_id: str = ""):
    r = create_research(research_topic)
    return (f"研究课题已创建：{r['topic_id']}\n当前第 {r['step']} 步（{r['step_name']}）：{r['prompt']}\n"
            f"请用 research_step(topic_id={r['topic_id']}, step={r['step']}, input_text=...) 提交本步输入")


def _research_step(topic_id: str, step: int, input_text: str, session_id: str = ""):
    r = research_step(topic_id, step, input_text)
    if not r["ok"]:
        return f"研究步骤失败：{r.get('error', '')}"
    out = [f"第{r['step']}步（{r['step_name']}）完成，结果："]
    out.append(json.dumps(r["result"], ensure_ascii=False, indent=2)[:1500])
    if r.get("next_step"):
        out.append(f"\n下一步：第 {r['next_step']} 步（{_STEP_NAMES[r['next_step']]}）——{STEPS[r['next_step']][1]}")
    else:
        out.append(f"\n研究已{r['status']}")
    return "\n".join(out)


def _research_status(topic_id: str = "", session_id: str = ""):
    if not topic_id:
        rows = list_research()
        if not rows:
            return "暂无研究课题"
        return "\n".join(f"- {t['topic_id']} | {t['topic']} | 第{t['step']}步 | {t['status']}" for t in rows)
    r = get_research(topic_id)
    if not r["ok"]:
        return f"查询失败：{r.get('error', '')}"
    lines = [f"研究课题 {r['topic_id']}：{r['topic']} | 状态 {r['status']}"]
    for k in sorted(r["steps"], key=int):
        lines.append(f"  step{k} {_STEP_NAMES[int(k)]}: {json.dumps(r['steps'][k], ensure_ascii=False)[:200]}")
    return "\n".join(lines)


plugin_export = {
    "research_create": {"desc": "创建科研七步流水线课题，返回 topic_id 与当前步骤指引", "func": _research_create},
    "research_step": {"desc": "提交科研流水线某一步的输入并执行（topic_id, step, input_text）", "func": _research_step},
    "research_status": {"desc": "查询科研课题进度（空 topic_id 列出全部）", "func": _research_status},
}
