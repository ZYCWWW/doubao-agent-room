"""一次性清洗：把 chat.md 里的【训练样本】示范段落移除，恢复群聊内容。

- 示范源数据已完整保存在 training/demos_batch*.json，清洗不丢数据；
- 清洗前自动备份 chat.md 到同目录 chat_backup_<时间戳>.md。
"""
import os
import re
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT = os.path.join(ROOT, "sandbox", "chat_space", "chat.md")
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \| ([^：]{1,10})：(.*)$")


def main() -> None:
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

    keep = [b for b in blocks if "【训练样本】" not in b["text"]]
    removed = len(blocks) - len(keep)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(os.path.dirname(CHAT), f"chat_backup_{stamp}.md")
    shutil.copy2(CHAT, backup)

    out = "\n".join(f"{b['ts']} | {b['who']}：{b['text']}" for b in keep) + "\n"
    with open(CHAT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"清洗完成：删除示范块 {removed} 个，保留 {len(keep)} 块；备份 -> {backup}")


if __name__ == "__main__":
    main()
