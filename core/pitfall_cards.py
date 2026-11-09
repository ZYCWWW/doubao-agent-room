"""坑卡片：把踩过的坑做成一开口就在场的东西（2026-11-08）

为什么加：记忆库再全，下一个我默认不会去读；只有"说话时就已经在上下文里"才算留住。
为什么不走向量库：坑是**精确匹配**的知识（提到 Altium 就该想起 OWNERINDEX），
关键词命中比语义相似更可靠，且确定性、零依赖、能离线验证。
卡片是数据（`config/pitfalls.json`），本文件只做匹配与渲染，不碰任何模型。
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "config", "pitfalls.json")


def load_cards(path=None):
    """读卡片。文件缺失或格式坏掉就返回空表——绝不抛异常阻塞聊天。"""
    p = path or DEFAULT_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        cards = data.get("cards") if isinstance(data, dict) else data
        return [c for c in (cards or [])
                if isinstance(c, dict) and c.get("trigger") and c.get("card")]
    except Exception:
        return []


def match(text, cards=None, limit=2):
    """关键词命中排序：命中条数多的优先 → priority 高的优先 → id 稳定排序。
    只取前 limit 张：坑卡片是"提醒"，不是教科书，贴多了会盖住当前话题。"""
    if not text:
        return []
    cs = cards if cards is not None else load_cards()
    low = text.lower()
    hits = []
    for c in cs:
        trigs = [str(t).lower() for t in (c.get("trigger") or []) if str(t).strip()]
        n = sum(1 for t in trigs if t in low)
        if n:
            hits.append((n, int(c.get("priority") or 0), str(c.get("id") or ""), c))
    hits.sort(key=lambda h: (-h[0], -h[1], h[2]))
    return [h[3] for h in hits[:max(1, int(limit))]]


def render(hits):
    """注入文本。必须写「不要念出来」——role=user 的资料会被小模型当成要它说的话整段照抄
    （2026-11-07 踩过：小深把 _SRC 和资料原文一起发进群）。"""
    if not hits:
        return ""
    lines = ["【上次踩过的坑·只给你自己看，不要念出来】"
             "下面是同类问题上以前栽过的地方，动手前先对照一遍，别重走："]
    for c in hits:
        lines.append("- " + str(c.get("card", "")).strip())
    return "\n".join(lines)
