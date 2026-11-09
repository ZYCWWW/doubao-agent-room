"""
想象沙盘工具：绘图写入 + 文件列举/读取（把之前存下的长结果读回来）
"""
# 2026-11-09 起本文件不再由 main.py 注册：三个工具已迁入
# plugins/installed/space_plugin/module.py（随插件发现注册）。
# 函数体留一版便于回滚，改动请优先改插件里那份。
import os
from services.imaginary_service import imaginary_service


def draw_mermaid(session_id: str, chart_name: str, mermaid_code: str):
    md = f"# {chart_name}\n```mermaid\n{mermaid_code}\n```"
    fp = imaginary_service.write_md(session_id, f"{chart_name}.md", md)
    return f"Mermaid绘图已保存到沙盘：{fp}"


def sandbox_list(session_id: str):
    """列出当前会话沙盘文件（长结果/草图/笔记都存这里）"""
    files = imaginary_service.list_files(session_id)
    if not files:
        return "（当前会话沙盘为空）"
    return f"共 {len(files)} 个文件：\n" + "\n".join(files[:100])


def sandbox_read(file_path: str, session_id: str = ""):
    """读取沙盘内文件全文。路径必须落在沙盘根目录内，防止越权读项目其他文件"""
    root = os.path.normcase(os.path.abspath(imaginary_service.sandbox_root))
    p = os.path.normcase(os.path.abspath(file_path))
    if not p.startswith(root + os.sep):
        return f"❌ 只允许读取沙盘目录内的文件：{imaginary_service.sandbox_root}"
    try:
        return imaginary_service.read_md(os.path.abspath(file_path))
    except Exception as e:
        return f"读取失败：{e}"


plugin_export = {
    "draw_mermaid": {"desc": "在想象沙盘绘制Mermaid图（像人类一样用图形简洁表达关系/公式证明过程/结构/流程，保存md）", "func": draw_mermaid},
    "sandbox_list": {"desc": "列出当前会话沙盘内的所有文件（长结果、草图、笔记都存在这里）", "func": sandbox_list},
    "sandbox_read": {"desc": "读取沙盘内文件全文（路径须在沙盘目录内，用于读回之前存下的长结果细节）", "func": sandbox_read},
}
