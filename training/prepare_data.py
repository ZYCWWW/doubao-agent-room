"""从项目现有数据抽训练样本 → training/data/{train,val}.jsonl（纯标准库，无需联网）。

用法：
    python training/prepare_data.py --limit 800
样本来源：
  1. chat.md 的本地模型回合
  2. 归档的 hypothesis/*.md 猜想结论（猜想库已于 2026-11-08 移除，数据移入 _archive/）
  3. chat.md 里豆包按 README §四 产出的【训练样本】示范段落（豆包教学不入闲聊语料，但单列抽取）
"""
import argparse
import glob
import json
import os
import random
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT = os.path.join(ROOT, "sandbox", "chat_space", "chat.md")
HYP = os.path.join(ROOT, "_archive", "hypothesis_removed_20261108", "sandbox_hypothesis")
OUT = os.path.join(ROOT, "training", "data")
SYS = "你是这台电脑上的 AI 伙伴，说话简短（120字内）、口语化，不冒用别人的名字。"

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \| ([^：]{1,10})：(.*)$")
SKIP_SPEAKERS = {"豆包"}          # 豆包的广播/教学不入闲聊训练集（示范段落单独抽取）
LOCAL_SPEAKERS = {"Llama", "DeepSeek", "Qwen", "小深"}
DEMO_MARK = "【训练样本】"


def _blocks():
    """把 chat.md 聚合成消息块 [(ts, who, text)]；多行消息（示范/长文）合并进所属块。"""
    if not os.path.exists(CHAT):
        return []
    lines = [l.rstrip() for l in open(CHAT, encoding="utf-8").read().splitlines() if l.strip()]
    blocks, cur = [], None
    for raw in lines:
        m = LINE_RE.match(raw)
        if m:
            if cur:
                blocks.append(cur)
            ts, who, text = m.groups()
            cur = {"ts": ts, "who": who.strip(), "text": text.strip()}
        elif cur:
            cur["text"] += "\n" + raw.strip()
    if cur:
        blocks.append(cur)
    return blocks


def read_chat(limit):
    parsed = [b for b in _blocks()
              if b["who"] not in SKIP_SPEAKERS and not b["text"].startswith(("📡", "🔕", "📖"))]
    samples, seen = [], set()
    for i, b in enumerate(parsed):
        who, text = b["who"], b["text"]
        if who not in LOCAL_SPEAKERS or len(text) < 20 or text in seen:
            continue
        ctx = [f"{p['who']}：{p['text']}" for p in parsed[max(0, i - 3):i]]
        if not ctx:
            continue
        seen.add(text)
        samples.append({"messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": "刚才的对话：\n" + "\n".join(ctx) + "\n\n请接着说一句。"},
            {"role": "assistant", "content": text},
        ]})
        if len(samples) >= limit:
            break
    return samples


def read_doubao_demos(limit):
    """抽豆包产的【训练样本】示范段落（3–6 轮），转成多轮 messages 样本。"""
    out = []
    for b in _blocks():
        if b["who"] != "豆包" or DEMO_MARK not in b["text"]:
            continue
        body = b["text"].split(DEMO_MARK, 1)[1]
        msgs = []
        for ln in body.split("\n"):
            ln = ln.strip()
            if ln.startswith("###"):
                continue
            if ln.startswith("用户："):
                msgs.append({"role": "user", "content": ln[3:].strip()})
            elif ln.startswith("助手："):
                msgs.append({"role": "assistant", "content": ln[3:].strip()})
        # 质量门槛：首轮 user、末轮 assistant、至少 4 轮；助手回答 ≥10 字，用户问句 ≥4 字
        if len(msgs) < 4 or msgs[0]["role"] != "user" or msgs[-1]["role"] != "assistant":
            continue
        if any(m["role"] == "assistant" and len(m["content"]) < 10 for m in msgs):
            continue
        if any(m["role"] == "user" and len(m["content"]) < 4 for m in msgs):
            continue
        out.append({"messages": [{"role": "system", "content": SYS}] + msgs})
        if len(out) >= limit:
            break
    return out


def read_demo_files(limit):
    """从 training/demos_batch*.json 直接读示范源数据（不依赖会被截断的 chat.md）。"""
    out = []
    files = sorted(glob.glob(os.path.join(ROOT, "training", "demos_batch*.json")))
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                demos = json.load(f)
        except Exception:
            continue
        for d in demos:
            msgs = []
            for u, a in d.get("turns", []):
                msgs.append({"role": "user", "content": str(u).strip()})
                msgs.append({"role": "assistant", "content": str(a).strip()})
            if len(msgs) < 4 or msgs[0]["role"] != "user" or msgs[-1]["role"] != "assistant":
                continue
            if any(m["role"] == "assistant" and len(m["content"]) < 10 for m in msgs):
                continue
            if any(m["role"] == "user" and len(m["content"]) < 4 for m in msgs):
                continue
            out.append({"messages": [{"role": "system", "content": SYS}] + msgs})
            if len(out) >= limit:
                return out
    return out


def read_hypothesis(limit):
    out = []
    if not os.path.isdir(HYP):
        return out
    for fn in sorted(os.listdir(HYP)):
        if not fn.endswith(".md") or limit <= 0:
            continue
        try:
            txt = open(os.path.join(HYP, fn), encoding="utf-8").read()
        except OSError:
            continue
        mt = re.search(r"## 猜想\s*\n(.+)", txt)
        mc = re.search(r"## 结论\s*\n(.*?)(?=\n## |\Z)", txt, re.S)
        title, concl = (mt.group(1).strip() if mt else ""), (mc.group(1).strip() if mc else "")
        if len(title) < 6 or len(concl) < 10:
            continue
        out.append({"messages": [
            {"role": "system", "content": "你是严谨的科研助手，回答要点明确、给证据。"},
            {"role": "user", "content": f"猜想：{title}\n请给出结论与依据。"},
            {"role": "assistant", "content": concl[:400]},
        ]})
        limit -= 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--val-ratio", type=float, default=0.1)
    args = ap.parse_args()

    data = (read_chat(args.limit)
            + read_hypothesis(max(50, args.limit // 5))
            + read_demo_files(args.limit)
            + read_doubao_demos(args.limit))
    # 去重（同一段示范发了多次只保留一次）
    dedup, seen_keys = [], set()
    for s in data:
        key = json.dumps(s["messages"][1:], ensure_ascii=False, sort_keys=True)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        dedup.append(s)
    data = dedup
    random.seed(42)
    random.shuffle(data)
    n_val = max(1, int(len(data) * args.val_ratio))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "train.jsonl"), "w", encoding="utf-8") as f:
        for s in data[n_val:]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT, "val.jsonl"), "w", encoding="utf-8") as f:
        for s in data[:n_val]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"samples={len(data)} train={len(data) - n_val} val={n_val} -> {OUT}")


if __name__ == "__main__":
    main()
