"""通用：把 _pages/<conversation_id>_p*.json 合并成逐字 Markdown。"""
import glob
import json
import os
import sys

RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "conversations", "raw")
PAGES = os.path.join(RAW, "_pages")


def dump_cell(item):
    role = item.get("role") or item.get("type") or "?"
    ts = item.get("created_at") or ""
    parts = []
    for c in item.get("content") or []:
        if c.get("type") == "text":
            parts.append(c.get("text") or "")
        else:
            parts.append("```json\n" + json.dumps(c, ensure_ascii=False) + "\n```")
    body = "\n".join(parts)
    who = {"user": "他", "assistant": "我"}.get(role, role)
    return f"**{who}**　`{ts}`\n\n{body}\n"


def main(cid):
    files = sorted(glob.glob(os.path.join(PAGES, f"{cid}_p*.json")))
    if not files:
        print("NO_PAGES", cid)
        return
    turns = {}
    meta = None
    pages_info = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
        if meta is None:
            meta = d.get("conversation") or {}
        p = d.get("page") or {}
        pages_info.append((os.path.basename(f), len(d.get("turns") or []), p.get("next_cursor"), p.get("has_more")))
        for t in d.get("turns") or []:
            turns[t.get("sequence")] = t
    seqs = sorted(turns)
    n_user = sum(1 for s in seqs for it in turns[s].get("items", []) if it.get("role") == "user")
    n_asst = sum(1 for s in seqs for it in turns[s].get("items", []) if it.get("role") == "assistant")

    title = meta.get("title") or ""
    out = [f"# 对话原文 · {cid} · {title}", ""]
    out.append(f"- 段数 {len(seqs)}（用户 {n_user} / 我 {n_asst}）")
    out.append(f"- 页文件 {len(files)} 个；全部 has_more=false" if all(not x[3] for x in pages_info) else f"- 页文件 {len(files)} 个")
    for name, cnt, cur, more in pages_info:
        out.append(f"  - `{name}`：{cnt} 段，next_cursor={cur!r}，has_more={more}")
    out.append("- 逐字原文，未改写、未截断；`sequence` 原样保留")
    out.append("")
    out.append("---")
    out.append("")
    for s in seqs:
        t = turns[s]
        out.append(f"## seq {s} · {t.get('started_at')}")
        out.append("")
        for it in t.get("items", []):
            out.append(dump_cell(it))
            out.append("")
        out.append("---")
        out.append("")

    dst = os.path.join(RAW, f"{cid}_对话原文.md")
    text = "\n".join(out)
    with open(dst, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(text)
    print("WROTE", dst, os.path.getsize(dst), "bytes", len(text.splitlines()), "lines")


if __name__ == "__main__":
    main(sys.argv[1])
