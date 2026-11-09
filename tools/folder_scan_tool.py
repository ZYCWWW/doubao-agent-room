"""
文件夹扫描工具，受ACL白名单管控
⚠️ 2026-11-09 起本文件不再由 main.py 注册：scan_folder 已迁到
   plugins/installed/reading_plugin。这里留一版便于回滚。
"""
import os
from core.vision_acl_mgr import vision_acl_mgr
from core.config_loader import config_loader

def scan_folder(path: str, session_id: str):
    permit, reason = vision_acl_mgr.check_action_permit(path, "scan_folder")
    if not permit:
        return f"❌ 拒绝扫描：{reason}"
    max_depth = config_loader["vision_acl"]["max_scan_depth"]
    out = []
    for root, dirs, files in os.walk(path):
        rel = os.path.relpath(root, path)
        depth = len(rel.split(os.sep))
        if depth > max_depth:
            break
        for f in files:
            out.append(os.path.join(root, f))
    return "\n".join(out[:100])

plugin_export = {"scan_folder": {"desc": "在白名单内遍历文件夹文件", "func": scan_folder}}
