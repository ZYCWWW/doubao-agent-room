# core/mode/risk_monitor.py
"""
风险前置检查：steady 下对高危工具打分，超阈值 → 返回确认提示（不执行）
"""
RISK_RULES = {
    "desktop_action": {"base": 0.6, "dangerous_payload": ("click", "press", "type_text", "hotkey")},
    "write_file": {"base": 0.7},
    "delete_file": {"base": 0.9},
    "launch_app": {"base": 0.5},
    "mouse_keyboard": {"base": 0.8},
    "vl_analyze_image": {"base": 0.0},
}

RISK_THRESHOLD = 0.6


def pre_check(mode: str, tool_name: str, args: dict) -> tuple[bool, str]:
    """
    执行前风险检查
    :return: (是否放行, 原因)
    """
    if mode != "steady":
        return True, "inquiry 模式放行"
    rule = RISK_RULES.get(tool_name)
    if not rule:
        return True, "无风险规则"
    score = rule["base"]
    dangerous = rule.get("dangerous_payload", ())
    for d in dangerous:
        if d in str(args or {}):
            score = min(1.0, score + 0.25)
            break
    if score >= RISK_THRESHOLD:
        return False, (f"风险检查拦截：工具 {tool_name} 风险分 {score:.1f} ≥ {RISK_THRESHOLD}。"
                       f"请确认后执行（steady 模式）")
    return True, f"风险分 {score:.1f} 低于阈值"


def risk_score(tool_name: str, args: dict) -> float:
    rule = RISK_RULES.get(tool_name)
    if not rule:
        return 0.0
    score = rule["base"]
    for d in rule.get("dangerous_payload", ()):
        if d in str(args or {}):
            score = min(1.0, score + 0.25)
    return round(score, 2)
