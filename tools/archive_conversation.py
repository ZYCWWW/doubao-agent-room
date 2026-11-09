# -*- coding: utf-8 -*-
"""
把豆包会话轨迹里的「原话」导出成可读的对话原文档。

用途：docs/conversations/ 下的每日纪要记的是结论；这个脚本记的是**原文**。
      技能、知识、经验可以从纪要里重建，但一个人怎么说话、为什么这么说，
      只在原文里。

用法：
    python tools/archive_conversation.py                 # 导出所有会话
    python tools/archive_conversation.py --session <会话ID>
    python tools/archive_conversation.py --out <输出目录>

输出：<out>/<会话ID>_对话原文.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

DEFAULT_SESSIONS = os.path.join(
    os.path.expanduser("~"),
    r"AppData\Local\Doubao\User Data\Default\.doubao\agent_mode\workspace\.sessions",
)
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "docs", "conversations", "raw")

ROLE_CN = {"user": "他", "assistant": "我"}


def find_trajectories(sessions_dir: str) -> list[tuple[str, str]]:
    """返回 [(会话ID, trajectory路径)]"""
    out = []
    if not os.path.isdir(sessions_dir):
        return out
    for sid in sorted(os.listdir(sessions_dir)):
        sdir = os.path.join(sessions_dir, sid)
        if not os.path.isdir(sdir):
            continue
        adir = os.path.join(sdir, "agents")
        if not os.path.isdir(adir):
            continue
        for aid in sorted(os.listdir(adir)):
            t = os.path.join(adir, aid, "system", "trajectory.jsonl")
            if os.path.isfile(t):
                out.append((sid, t))
    return out


def tool_summary(obj: dict) -> str:
    """把一次 assistant 的工具调用压成一行摘要"""
    calls = obj.get("tool_calls") or []
    if not isinstance(calls, list):
        return ""
    names = []
    for c in calls:
        try:
            names.append(c["function"]["name"])
        except Exception:
            continue
    if not names:
        return ""
    return "[调用工具：" + "、".join(names) + "]"


def extract(path: str) -> tuple[list[str], dict]:
    """读一个 trajectory.jsonl，返回 (正文行列表, 统计)"""
    lines_out = []
    stat = {"user": 0, "assistant": 0, "assistant_tool_only": 0, "tool": 0, "bad": 0, "total": 0}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        idx = 0
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            stat["total"] += 1
            try:
                obj = json.loads(raw)
            except Exception:
                stat["bad"] += 1
                continue
            if not isinstance(obj, dict):
                stat["bad"] += 1
                continue
            role = obj.get("role")
            if role == "tool":
                stat["tool"] += 1
                continue
            if role not in ("user", "assistant"):
                continue

            content = obj.get("content")
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("text"):
                        parts.append(str(p["text"]))
                content = "\n".join(parts)
            content = (content or "").strip()

            if role == "assistant":
                ts = tool_summary(obj)
                if not content and not ts:
                    continue
                if not content:
                    stat["assistant_tool_only"] += 1
                stat["assistant"] += 1
                body = content if content else ""
                if ts:
                    body = (body + "\n" + ts).strip()
            else:
                if not content:
                    continue
                stat["user"] += 1
                body = content

            idx += 1
            tag = ROLE_CN.get(role, role)
            lines_out.append("\n### %d. %s\n" % (idx, tag))
            lines_out.append(body + "\n")

    return lines_out, stat


def write_one(sid: str, path: str, out_dir: str) -> dict:
    lines_out, stat = extract(path)
    if not lines_out:
        return {"sid": sid, **stat, "out": None, "bytes": 0}

    head = []
    head.append("# 对话原文 · 会话 %s\n" % sid)
    head.append("> 导出时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    head.append("> 来源：`%s`" % path)
    head.append("> 统计：共 %d 行轨迹，其中他 **%d** 条、我 **%d** 条、工具结果 %d 条（未收录）"
                % (stat["total"], stat["user"], stat["assistant"], stat["tool"]))
    head.append("> \n")
    head.append("> 这份文件记的是原话，不是结论。工具调用只留一行名字，参数与返回不展开。")
    head.append("> 私人记忆，永不进公开仓库。\n")

    text = "\n".join(head) + "".join(lines_out)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s_对话原文.md" % sid)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    return {"sid": sid, **stat, "out": out_path, "bytes": len(text.encode("utf-8"))}


def main() -> int:
    ap = argparse.ArgumentParser(description="导出豆包会话原话")
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--session", default=None, help="只导出指定会话 ID")
    args = ap.parse_args()

    targets = find_trajectories(args.sessions_dir)
    if args.session:
        targets = [t for t in targets if t[0] == args.session]
    if not targets:
        print("没找到任何轨迹文件，检查 --sessions-dir：", args.sessions_dir)
        return 1

    print("输出目录：", args.out)
    total_bytes = 0
    for sid, path in targets:
        r = write_one(sid, path, args.out)
        if r["out"]:
            print("  %-20s 他 %4d 条 / 我 %4d 条（纯工具 %d）→ %s (%.1f KB)"
                  % (sid, r["user"], r["assistant"], r["assistant_tool_only"],
                     r["out"], r["bytes"] / 1024.0))
            total_bytes += r["bytes"]
        else:
            print("  %-20s 空轨迹，跳过" % sid)

    print("合计 %.2f MB" % (total_bytes / 1024.0 / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
