"""评测基线：让本地模型回答 val.jsonl，算规则分；有云端 key 时再请云端当裁判。

用法：
    python training/eval.py --model qwen3.5:4b --file training/data/val.jsonl --limit 20
输出：每条样本的规则指标 + 汇总（长度/空答/复读/关键词重合）。
"""
import argparse
import json
import os
import re
import sys
import urllib.request

OLLAMA = "http://127.0.0.1:11434/api/chat"


def ask_local(model, messages, timeout=120):
    body = json.dumps({"model": model, "messages": messages, "stream": False,
                       "options": {"num_ctx": 4096}}).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["message"]["content"].strip()


def rules(answer, reference):
    ans = (answer or "").strip()
    ref = (reference or "").strip()
    rep = 0.0
    if len(ans) > 20:
        grams = [ans[i:i + 6] for i in range(0, len(ans) - 6, 3)]
        rep = 1 - len(set(grams)) / max(1, len(grams))
    kw_hit = None
    if ref:
        keys = set(re.findall(r"[\u4e00-\u9fa5]{2,4}", ref))
        kw_hit = round(len([k for k in keys if k in ans]) / max(1, len(keys)), 2)
    return {"chars": len(ans), "empty": not ans, "repeat": round(rep, 2), "kw_hit": kw_hit}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--file", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "data", "val.jsonl"))
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    if not os.path.exists(args.file):
        sys.exit(f"找不到 {args.file}，先跑 prepare_data.py")

    rows = []
    with open(args.file, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= args.limit:
                break
            s = json.loads(line)
            msgs = s["messages"][:2]          # system + user，不给参考答案
            ref = s["messages"][-1]["content"]
            try:
                ans = ask_local(args.model, msgs)
            except Exception as e:
                ans = ""
                print("ERR", str(e)[:100])
            rows.append({"i": i, **rules(ans, ref), "answer": ans[:80]})

    n = len(rows) or 1
    summary = {
        "model": args.model, "n": len(rows),
        "avg_chars": round(sum(r["chars"] for r in rows) / n, 1),
        "empty_rate": round(sum(1 for r in rows if r["empty"]) / n, 2),
        "avg_repeat": round(sum(r["repeat"] for r in rows) / n, 2),
        "avg_kw_hit": round(sum(r["kw_hit"] or 0 for r in rows) / n, 2),
    }
    print(json.dumps({"summary": summary, "rows": rows[:5]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
