"""把 training/demos_batch*.json 里的示范对话按 README §四 格式写入聊天室（chat.md）。

每段格式：
    【训练样本】
    ### 场景：<一句话>
    用户：…
    助手：…
    ...
带断点续写（training/demo_state.json 记已写条数），重复运行不会重复灌群。
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from services.agent_chat import chat_space  # noqa: E402


def fmt(demo: dict) -> str:
    body = "【训练样本】\n### 场景：" + demo["scene"]
    for u, a in demo["turns"]:
        body += f"\n用户：{u}\n助手：{a}"
    return body


def main() -> None:
    files = sorted(glob.glob(os.path.join(HERE, "demos_batch*.json")))
    demos = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            demos += json.load(fh)
    state_p = os.path.join(HERE, "demo_state.json")
    sent = 0
    if os.path.exists(state_p):
        try:
            with open(state_p, encoding="utf-8") as fh:
                sent = int(json.load(fh).get("sent", 0))
        except Exception:
            sent = 0
    total = len(demos)
    for d in demos[sent:]:
        r = chat_space.doubao_speak(fmt(d), max_len=2400)
        if not r.get("ok"):
            print(f"写入失败：{r}")
            break
        sent += 1
        with open(state_p, "w", encoding="utf-8") as fh:
            json.dump({"sent": sent}, fh)
        time.sleep(0.5)
    print(f"示范已写入 {sent}/{total} 段（累计）")


if __name__ == "__main__":
    main()
