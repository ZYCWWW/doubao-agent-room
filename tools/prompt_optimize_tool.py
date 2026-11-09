"""
提示词助手工具
- prompt_optimize：优化并对比提示词（不直接执行，先让用户确认）
- prompt_submit：把（确认后的）提示词交给 LLM 执行
"""
import json
from services.prompt_engineer import prompt_engineer


def prompt_optimize(prompt: str, style: str = "通用", **kwargs):
    """优化提示词并返回对比结果（不执行）"""
    try:
        result = prompt_engineer.optimize(prompt, style=style)
    except Exception as e:
        return json.dumps({"success": False, "error": f"优化异常: {e}"}, ensure_ascii=False)
    if not result.get("success"):
        return json.dumps(result, ensure_ascii=False)
    return prompt_engineer.compare(result["original"], result["optimized"], result.get("changes"))


def prompt_submit(prompt: str, model: str = None, system: str = None, **kwargs):
    """把提示词交给 LLM 执行"""
    try:
        result = prompt_engineer.submit(prompt, model=model, system=system)
    except Exception as e:
        return json.dumps({"success": False, "error": f"提交异常: {e}"}, ensure_ascii=False)
    if not result.get("success"):
        return json.dumps(result, ensure_ascii=False)
    return result["response"]


plugin_export = {
    "prompt_optimize": {
        "func": prompt_optimize,
        "desc": "提示词优化助手：优化用户提示词并返回原版/优化版对比与改进点，不直接执行。"
                "参数：prompt(待优化提示词), style(可选风格偏好)。",
    },
    "prompt_submit": {
        "func": prompt_submit,
        "desc": "将提示词交给 LLM 执行（用户确认优化结果后使用）。"
                "参数：prompt(提示词内容), model(可选指定模型), system(可选系统提示)。",
    },
}
