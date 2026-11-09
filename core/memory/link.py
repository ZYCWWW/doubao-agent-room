"""
记忆-沙盘双向溯源
- 记忆条目 metadata.related_sandbox_ref 指向沙盘文件
- 沙盘文件头部维护 backlinks 段（记忆 id 列表）
"""
import re
import os
from core.logger import root_logger

BACKLINK_MARK = "<!-- memory-backlinks -->"


def link_memory_to_sandbox(memory_id: str, sandbox_path: str) -> str:
    """记忆侧溯源：返回更新后的 related_sandbox_ref 值"""
    return sandbox_path


def add_sandbox_backlink(sandbox_path: str, memory_id: str) -> bool:
    """
    沙盘侧溯源：在 md 文件维护 backlinks 段
    返回是否写入成功（文件不存在或写入失败返回 False）
    """
    if not sandbox_path or not os.path.exists(sandbox_path):
        return False
    try:
        with open(sandbox_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        root_logger.warning(f"溯源读取沙盘失败 {sandbox_path}: {e}")
        return False

    if BACKLINK_MARK in content:
        # 更新已有 backlinks 段
        pattern = re.compile(
            re.escape(BACKLINK_MARK) + r"\n- .*?(?=\n<!--|\Z)", re.DOTALL)
        block = f"{BACKLINK_MARK}\n- {memory_id}"
        if pattern.search(content):
            content = pattern.sub(block, content, count=1)
        else:
            content += f"\n\n{block}\n"
    else:
        block = f"\n\n{BACKLINK_MARK}\n- {memory_id}\n"
        # 插到文档末尾（避免破坏正文结构）
        content = content.rstrip() + block

    try:
        with open(sandbox_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError as e:
        root_logger.warning(f"溯源写入沙盘失败 {sandbox_path}: {e}")
        return False


def get_sandbox_backlinks(sandbox_path: str) -> list:
    """读取沙盘文件的 backlinks 记忆 id 列表"""
    if not sandbox_path or not os.path.exists(sandbox_path):
        return []
    try:
        with open(sandbox_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []
    if BACKLINK_MARK not in content:
        return []
    m = re.search(re.escape(BACKLINK_MARK) + r"\n(.*?)(?=\n<!--|\Z)", content, re.DOTALL)
    if not m:
        return []
    return [line.strip().lstrip("- ").strip()
            for line in m.group(1).splitlines()
            if line.strip().startswith("- ")]
