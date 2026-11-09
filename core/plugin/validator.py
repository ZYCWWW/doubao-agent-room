# core/plugin/validator.py
"""
插件校验与安全：
- manifest 校验：name/version/desc 齐全
- 黑名单：危险函数名/模块导入拦截（防注入）
- 失败标记 disabled + 明确错误，不阻塞主循环
"""
import re

BLOCKED_IMPORTS = ("os.system", "subprocess", "shutil.rmtree", "__import__",
                   "eval(", "exec(", "pickle.load", "socket")
DANGEROUS_TOOLS = ("write_file", "delete_file", "launch_app", "mouse_keyboard",
                   "execute_command")


def validate_manifest(manifest: dict) -> tuple[bool, str]:
    """校验插件清单；返回 (是否通过, 原因)"""
    if not isinstance(manifest, dict):
        return False, "manifest 不是对象"
    for key in ("name", "version", "desc"):
        if not manifest.get(key):
            return False, f"manifest 缺少字段：{key}"
    return True, "ok"


def validate_code_safety(code: str) -> tuple[bool, list]:
    """扫描代码中的危险调用；返回 (是否通过, 命中列表)"""
    if not code:
        return True, []
    hits = [b for b in BLOCKED_IMPORTS if b in code]
    return (len(hits) == 0), hits


def validate_tools(tools: dict) -> tuple[bool, str]:
    """校验工具声明；返回 (是否通过, 原因)"""
    for name, spec in (tools or {}).items():
        if not name or not getattr(spec, "func", None):
            return False, f"工具声明不完整：{name}"
    return True, "ok"
