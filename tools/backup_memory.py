# -*- coding: utf-8 -*-
"""
每日记忆备份：把"会消失的东西"整体复制成一份带日期的快照。

备份三块（这三块是记忆，不是代码；代码在 git 里，不需要每天备）：
  1. agent_memory_db     向量记忆库（chromadb）
  2. docs/conversations  每日对话纪要 + raw 原文
  3. sandbox/chat_space  群聊流水、状态、按日归档、小屋记录

输出：<备份根目录>\\YYYYMMDD\\
      manifest.json 记录每个文件的来源、大小、sha1、失败清单
      同目录上一次的失败清单会保留，方便对照

滚动：默认保留最近 14 天，更早的日期目录自动删除（只删本脚本创建的日期目录）。

用法：
    python tools/backup_memory.py              # 备份今天
    python tools/backup_memory.py --dry        # 只清点不复制
    python tools/backup_memory.py --keep 30    # 保留 30 天

日志：logs/memory_backup.log
"""
import argparse
import hashlib
import json
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT = os.path.join(os.path.dirname(ROOT), "记忆备份")
LOG = os.path.join(ROOT, "logs", "memory_backup.log")

# 要备份的三块：源目录 -> 快照里的子目录名
SOURCES = {
    os.path.join(ROOT, "agent_memory_db"): "agent_memory_db",
    os.path.join(ROOT, "docs", "conversations"): "conversations",
    os.path.join(ROOT, "sandbox", "chat_space"): "chat_space",
}

# chat_space 里只留这些类型，避免把图片缓存、OCR 缓存也拖进来
CHAT_SPACE_EXT = {".md", ".json"}


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def sha1(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def iter_files(src: str):
    """遍历源目录，返回 [(绝对路径, 相对路径)]"""
    if not os.path.isdir(src):
        return
    for cur, _dirs, files in os.walk(src):
        for name in files:
            full = os.path.join(cur, name)
            rel = os.path.relpath(full, src)
            yield full, rel


def want(name: str) -> bool:
    """chat_space 只收 md/json；其余两块全收"""
    return os.path.splitext(name)[1].lower() in CHAT_SPACE_EXT


def backup(dry: bool, keep: int) -> dict:
    day = time.strftime("%Y%m%d")
    dst_root = os.path.join(BACKUP_ROOT, day)
    manifest = {
        "date": day,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": ROOT,
        "dst": dst_root,
        "sources": {},
        "files": [],
        "failed": [],
        "total_bytes": 0,
    }
    for src, sub in SOURCES.items():
        if not os.path.isdir(src):
            manifest["sources"][sub] = {"ok": False, "reason": "目录不存在", "count": 0}
            continue
        count = 0
        if not dry:
            os.makedirs(os.path.join(dst_root, sub), exist_ok=True)
        for full, rel in iter_files(src):
            if sub == "chat_space" and not want(rel):
                continue
            count += 1
            size = os.path.getsize(full)
            entry = {"src": os.path.relpath(full, ROOT), "sub": sub,
                     "rel": rel.replace("\\", "/"), "bytes": size}
            if not dry:
                target = os.path.join(dst_root, sub, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                try:
                    shutil.copy2(full, target)
                    entry["sha1"] = sha1(target)
                    manifest["total_bytes"] += size
                except Exception as e:
                    entry["error"] = str(e)[:200]
                    manifest["failed"].append(entry)
            manifest["files"].append(entry)
        manifest["sources"][sub] = {"ok": True, "count": count}
        log(f"  {sub}: {count} 个文件{'（dry）' if dry else ' 已复制'}")

    if not dry:
        os.makedirs(dst_root, exist_ok=True)
        with open(os.path.join(dst_root, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 滚动清理：只删本目录下 8 位数字命名的日期目录
    if not dry and keep > 0:
        existing = [d for d in os.listdir(BACKUP_ROOT)
                    if os.path.isdir(os.path.join(BACKUP_ROOT, d)) and d.isdigit() and len(d) == 8]
        existing.sort(reverse=True)
        for old in existing[keep:]:
            path = os.path.join(BACKUP_ROOT, old)
            try:
                shutil.rmtree(path)
                log(f"  清理旧备份: {old}")
            except Exception as e:
                log(f"  清理失败 {old}: {str(e)[:100]}")

    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只清点不复制")
    ap.add_argument("--keep", type=int, default=14, help="保留最近多少天的备份（0=不清理）")
    args = ap.parse_args()

    log(f"--- 记忆备份开始{'（dry）' if args.dry else ''} keep={args.keep} ---")
    m = backup(args.dry, args.keep)
    for sub, info in m["sources"].items():
        log(f"{sub}: {'OK' if info.get('ok') else info.get('reason')} / {info.get('count', 0)} 文件")
    log(f"合计 {len(m['files'])} 个文件, {m['total_bytes'] / 1048576:.2f} MB, 失败 {len(m['failed'])} 个")
    for f in m["failed"][:10]:
        log(f"  失败: {f['src']} -> {f.get('error')}")
    log(f"输出: {m['dst']}")
    log("--- 记忆备份结束 ---")


if __name__ == "__main__":
    main()
