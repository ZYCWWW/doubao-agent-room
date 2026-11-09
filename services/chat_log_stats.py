# services/chat_log_stats.py
"""
聊天流水（chat.md）只读解析：给面板提供「本轮谁说了什么」的结构化视图。

为什么单独一个模块、为什么只读：
- chat.md 的行格式是 `2026-11-09 08:23 | Qwen：内容`，长发言会跨很多行（续行没有时间前缀），
  直接按行显示时，一段话会被视觉上拆成好几条。
- 但「在聊天引擎里加统计」属于聊天红线范围，所以这里**只读**：不写入、不清理、不改格式、
  不 import 任何会触发 LLM 的模块。面板要的是"谁在第几轮说了什么"，这些信息原文里都有。
"""
import os
import re
import time

# 时间 | 说话人：内容；说话人不允许含冒号和分隔符，长度限制避免把正文里的冒号吃进来
_ENTRY_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*[|｜]\s*([^：:|｜]{1,24}?)\s*[：:]\s*(.*)$"
)


def parse(path: str) -> list:
    """把 chat.md 解析成 [{day, time, ts, who, text}]；续行并入上一条的 text。"""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    entries = []
    for raw in lines:
        if not raw.strip():
            continue
        m = _ENTRY_RE.match(raw)
        if m:
            entries.append({"day": m.group(1), "time": m.group(2),
                            "ts": f"{m.group(1)} {m.group(2)}",
                            "who": m.group(3).strip(), "text": m.group(4).strip()})
        elif entries:
            # 没有时间前缀 = 上一条的续行（原生文件里长发言就是这样跨行的）
            entries[-1]["text"] = (entries[-1]["text"] + "\n" + raw.strip()).strip()
    return entries


def recent_round(entries: list) -> list:
    """最近一轮接龙：从末尾往前取，直到某个说话人重复出现为止（每人一次，按原顺序返回）。

    「一轮」在多人闲聊里没有显式标记，用"第一个重复者"当边界最直观：
    A→B→C→A 时，最后这 3 条就是一轮；如果最后是 A→B→A→B 这种两人来回，就取到 A 为止的 2 条。
    """
    seen = set()
    seg = []
    for e in reversed(entries):
        if e["who"] in seen:
            break
        seen.add(e["who"])
        seg.append(e)
    return list(reversed(seg))


def stats(entries: list, day: str = "") -> dict:
    """按说话人汇总：{who: {who, today, total, last_ts, last_text}}"""
    day = day or time.strftime("%Y-%m-%d")
    out = {}
    for e in entries:
        d = out.setdefault(e["who"], {"who": e["who"], "today": 0, "total": 0,
                                      "last_ts": "", "last_text": ""})
        d["total"] += 1
        if e["day"] == day:
            d["today"] += 1
        d["last_ts"] = e["ts"]
        d["last_text"] = e["text"]
    return out
