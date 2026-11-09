"""
想象沙盘快照与回滚
- snapshot：把当前全部节点打包复制到 snapshots/<ts>/（或指定分支）
- branch：从快照创建分支
- rollback：回滚到指定快照
"""
import os
import time
import shutil
from core.imagination.node import nodes_dir
from core.logger import root_logger

SNAPSHOT_DIR_NAME = "snapshots"


def _snap_root(session_id: str) -> str:
    d = os.path.join(nodes_dir(session_id), "..", SNAPSHOT_DIR_NAME)
    return os.path.abspath(d)


def _ensure_snap_root(session_id: str) -> str:
    d = _snap_root(session_id)
    os.makedirs(d, exist_ok=True)
    return d


def snapshot(session_id: str, label: str = "") -> str:
    """创建全量快照，返回快照目录名（时间戳+label）"""
    nodes_d = nodes_dir(session_id)
    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_name = f"{ts}" + (f"_{label}" if label else "")
    dest = os.path.join(_ensure_snap_root(session_id), snap_name)
    if os.path.exists(dest):
        dest = dest + f"_{int(time.time() * 1000) % 10000}"
    if os.path.isdir(nodes_d):
        shutil.copytree(nodes_d, dest)
    else:
        os.makedirs(dest, exist_ok=True)
    root_logger.info(f"沙盘快照 sid={session_id} snap={os.path.basename(dest)}")
    return os.path.basename(dest)


def list_snapshots(session_id: str) -> list:
    """列出快照：{name, created_ts}（按时间倒序）"""
    d = _snap_root(session_id)
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        fp = os.path.join(d, name)
        if os.path.isdir(fp):
            out.append({"name": name, "created_ts": os.path.getmtime(fp)})
    out.sort(key=lambda x: x["created_ts"], reverse=True)
    return out


def rollback(session_id: str, snapshot_name: str) -> bool:
    """回滚到指定快照（先自动留一个恢复点快照）"""
    src = os.path.join(_snap_root(session_id), snapshot_name)
    if not os.path.isdir(src):
        root_logger.warning(f"回滚失败：快照不存在 {snapshot_name}")
        return False
    # 先保留当前状态作为恢复点
    snapshot(session_id, "pre_rollback")
    nodes_d = nodes_dir(session_id)
    # 清空当前节点
    if os.path.isdir(nodes_d):
        for fn in os.listdir(nodes_d):
            p = os.path.join(nodes_d, fn)
            os.remove(p) if os.path.isfile(p) else shutil.rmtree(p)
    else:
        os.makedirs(nodes_d, exist_ok=True)
    # 复制快照内容
    for fn in os.listdir(src):
        shutil.copy2(os.path.join(src, fn), os.path.join(nodes_d, fn))
    root_logger.info(f"沙盘回滚 sid={session_id} snap={snapshot_name}")
    return True


def branch(session_id: str, snapshot_name: str, new_sid: str) -> bool:
    """从快照复制到新会话作为分支起点"""
    src = os.path.join(_snap_root(session_id), snapshot_name)
    if not os.path.isdir(src):
        return False
    dest = nodes_dir(new_sid)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    for fn in os.listdir(src):
        shutil.copy2(os.path.join(src, fn), os.path.join(dest, fn))
    root_logger.info(f"沙盘分支 sid={session_id} snap={snapshot_name} → {new_sid}")
    return True
