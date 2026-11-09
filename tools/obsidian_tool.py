# tools/obsidian_tool.py
"""Obsidian 同步工具：obsidian_export（沙盘 → vault）"""
from core.obsidian_adapter import export_sandbox, export_all, build_index


def _obsidian_export(vault_path: str, session_id: str = "", session_id_arg: str = "",
                     all_sessions: bool = False, session_id_inject: str = ""):
    sid = session_id or session_id_arg or session_id_inject or ""
    if all_sessions or not sid:
        results = export_all(vault_path)
        ok_n = sum(1 for r in results if r.get("ok"))
        idx = build_index(vault_path)
        return f"已同步 {ok_n}/{len(results)} 个会话到 {vault_path}\n索引：{idx}"
    r = export_sandbox(sid, vault_path)
    if not r["ok"]:
        return f"导出失败：{r.get('error', '')}"
    idx = build_index(vault_path)
    return f"会话 {sid} 已导出 {r['exported']} 篇 → {r['target']}\n索引：{idx}"


plugin_export = {
    "obsidian_export": {"desc": "把沙盘 MD 笔记同步到 Obsidian vault（vault_path 必填；session_id 指定会话，留空同步全部）", "func": _obsidian_export},
}
