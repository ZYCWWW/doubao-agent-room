# core/mode/permission.py
"""
模式权限矩阵：任务分类决定工具/能力可用性
steady：画布仅用户发起、辩论禁止、未知点击禁止、科研自动发起禁止
inquiry：画布/辩论自动、未知点击带置信度、科研自动
（hypothesis_verify 能力域随猜想库于 2026-11-08 移除）
"""
PERMISSION = {
    # 注意：browser_click_unknown 是"点击未知目标"这类**动作类别**，不是已下线的
    # browser_click 工具；工作台里点没识别清楚的按钮同样归它管，故保留原名。
    "steady": {
        "canvas": "user_only",
        "debate": "forbidden",
        "browser_click_unknown": "forbidden",
        "memory_edit": "risk_check",
        "research": "forbidden_auto",
    },
    "inquiry": {
        "canvas": "auto",
        "debate": "auto",
        "browser_click_unknown": "allowed_with_confidence",
        "memory_edit": "risk_check",
        "research": "auto",
    },
}

# 工具名 → 能力域映射
TOOL_DOMAIN = {
    "debate_start": "debate",
    "debate_speak": "debate",
    "debate_extract": "debate",
    "llm_discuss": "debate",
    "research_create": "research",
    "research_step": "research",
    "run_toolchain": None,   # 由链内容决定，主循环另行处理
}


def check(mode: str, tool_name: str) -> tuple[bool, str]:
    """工具级模式检查；返回 (是否允许, 原因)"""
    domain = TOOL_DOMAIN.get(tool_name)
    if domain is None:
        return True, "无模式限制"
    rule = PERMISSION.get(mode, {}).get(domain, "auto")
    if rule == "forbidden":
        return False, f"模式[{mode}]禁止：{domain}"
    if rule == "forbidden_auto":
        return False, f"模式[{mode}]禁止自动发起 {domain}（需用户明确要求）"
    if rule == "user_only":
        return False, f"模式[{mode}]下 {domain} 仅用户主动发起"
    return True, f"模式[{mode}]允许 {domain}"


def mode_prompt_segment(mode: str) -> str:
    """系统 prompt 模式约束段"""
    if mode == "steady":
        return ("当前模式：steady（稳重执行）\n约束：禁止自主发起辩论/科研流水线；"
                "画布与记忆编辑仅用户明确要求时进行；未知目标点击需用户确认。")
    return ("当前模式：inquiry（大胆推理）\n允许自主发起辩论、科研流水线与画布探索；"
            "未知目标点击需置信度足够高。")
