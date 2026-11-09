# core/obsidian_adapter.py
"""
Obsidian 适配器：把沙盘 MD 笔记同步到 Obsidian vault（双向可读）
- export_sandbox：复制某会话沙盘 md → <vault>/agent_sandbox/<sid>/
- export_all：全量同步
- build_index：生成 vault 索引页（按会话+时间排序）
- 不删除目标库任何文件；同名文件加时间戳后缀避免覆盖
"""
import os
import shutil
import time


def _vault_root(vault_path: str) -> str:
    root = os.path.join(vault_path, "agent_sandbox")
    os.makedirs(root, exist_ok=True)
    return root


def export_sandbox(sid: str, vault_path: str,
                   sandbox_root: str = None) -> dict:
    """导出单个会话沙盘 md 到 Obsidian vault"""
    if sandbox_root is None:
        from core.config_loader import config_loader
        sandbox_root = config_loader["sandbox"]["root_path"]
    src = os.path.join(sandbox_root, sid)
    if not os.path.isdir(src):
        return {"ok": False, "error": f"沙盘会话不存在：{sid}"}
    dst = os.path.join(_vault_root(vault_path), sid)
    os.makedirs(dst, exist_ok=True)
    exported = 0
    for root, _dirs, fnames in os.walk(src):
        for fn in fnames:
            if not fn.lower().endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), src)
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out):
                # 同名文件加时间戳后缀，避免覆盖目标库内容
                base, ext = os.path.splitext(fn)
                out = os.path.join(os.path.dirname(out), f"{base}_{int(time.time())}{ext}")
            shutil.copy2(os.path.join(root, fn), out)
            exported += 1
    return {"ok": True, "sid": sid, "exported": exported, "target": dst}


def export_all(vault_path: str, sandbox_root: str = None) -> list:
    if sandbox_root is None:
        from core.config_loader import config_loader
        sandbox_root = config_loader["sandbox"]["root_path"]
    results = []
    if not os.path.isdir(sandbox_root):
        return results
    for sid in os.listdir(sandbox_root):
        if os.path.isdir(os.path.join(sandbox_root, sid)):
            results.append(export_sandbox(sid, vault_path, sandbox_root))
    return results


def build_index(vault_path: str) -> str:
    """生成 vault 索引页（agent_sandbox/index.md）"""
    root = _vault_root(vault_path)
    lines = ["# Agent 沙盘笔记索引", "",
             f"更新：{time.strftime('%Y-%m-%d %H:%M')}", ""]
    for sid in sorted(os.listdir(root), reverse=True):
        sdir = os.path.join(root, sid)
        if not os.path.isdir(sdir):
            continue
        mds = [f for f in os.listdir(sdir) if f.lower().endswith(".md")]
        lines.append(f"## 会话 {sid}（{len(mds)} 篇）")
        for f in mds:
            lines.append(f"- [[{sid}/{f.replace('.md', '')}|{f}]]")
        lines.append("")
    idx = os.path.join(root, "index.md")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return idx
