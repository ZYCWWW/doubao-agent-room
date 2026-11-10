# services/chat_archive.py
"""
闲聊记录归档（数据治理）
- chat.md 只保留最近 keep_recent 行，更早的行移到 sandbox/chat_space/archive/
- 同步修正 chat_summary.json 里的行指针（line / long_line），避免摘要错位
- 用与 _locked_append 相同的跨进程锁，避免和长聊/面板写入打架
"""
import json
import os
import re

from core.logger import root_logger

# 行首日期，如 "2026-11-09 08:23 | Qwen：..."；续行没有日期前缀
_DAY_PREFIX = re.compile(r"^(20\d{2}-\d{2}-\d{2}) ")

CHAT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "sandbox", "chat_space")
CHAT_MD = os.path.join(CHAT_ROOT, "chat.md")
SUMMARY_FILE = os.path.join(CHAT_ROOT, "chat_summary.json")
ARCHIVE_DIR = os.path.join(CHAT_ROOT, "archive")
_LOCK_DIR = os.path.join(os.path.dirname(CHAT_ROOT), ".locks")
_LOCK_FILE = os.path.join(_LOCK_DIR, "chat_append.lock")

KEEP_RECENT = 200      # 保留最近多少行
MIN_LINES = 400        # 少于该行数不归档


def _acquire(fd):
    import msvcrt
    os.lseek(fd, 0, 0)
    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)


def _release(fd):
    import msvcrt
    try:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _shift_pointers(moved: int, remaining: int) -> None:
    """归档后把摘要行指针整体前移，防止「摘要从第 N 行继续」错位"""
    if not os.path.exists(SUMMARY_FILE):
        return
    try:
        with open(SUMMARY_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return
    for key in ("line", "long_line"):
        try:
            st[key] = max(0, min(int(st.get(key, 0)) - moved, remaining))
        except (TypeError, ValueError):
            st[key] = 0
    try:
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        root_logger.warning(f"[归档] 摘要指针修正失败: {e}")


def _group_by_day(lines: list) -> dict:
    """把发言行按所属日期分组。
    续行（无日期前缀的长发言）跟随上一条的日期；开头没有日期的归到 nodate。
    """
    groups = {}
    cur = None
    for line in lines:
        m = _DAY_PREFIX.match(line)
        if m:
            cur = m.group(1)
        groups.setdefault(cur or "nodate", []).append(line)
    return groups


def _append_day_file(day: str, lines: list) -> str:
    """按日期追加到 archive/chat_YYYYMMDD.md（同一天多次归档合进一个文件）"""
    name = "chat_nodate.md" if day == "nodate" else f"chat_{day.replace('-', '')}.md"
    path = os.path.join(ARCHIVE_DIR, name)
    is_new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# {day} 群聊归档\n\n")
        f.write("\n".join(lines) + "\n")
    return name


def archive(keep_recent: int = KEEP_RECENT, min_lines: int = MIN_LINES) -> dict:
    """把 chat.md 的前缀按发言日期归档到 archive/，返回 {archived, remaining, files}"""
    if not os.path.exists(CHAT_MD):
        return {"archived": 0, "remaining": 0, "reason": "no chat.md"}
    fd = None
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)
        fd = os.open(_LOCK_FILE, os.O_RDWR | os.O_CREAT)
        _acquire(fd)
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return {"archived": 0, "remaining": 0, "reason": "lock busy"}
    try:
        with open(CHAT_MD, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        total = len(lines)
        if total <= max(min_lines, keep_recent):
            return {"archived": 0, "remaining": total, "reason": "below threshold"}
        moved = total - keep_recent
        head, tail = lines[:moved], lines[moved:]
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        files = []
        for day, day_lines in _group_by_day(head).items():
            files.append(_append_day_file(day, day_lines))
        with open(CHAT_MD, "w", encoding="utf-8") as f:
            f.write("\n".join(tail) + "\n")
        _shift_pointers(moved, len(tail))
        root_logger.info(f"[归档] chat.md 归档 {moved} 行 → {', '.join(files)}，保留 {len(tail)} 行")
        return {"archived": moved, "remaining": len(tail), "files": files}
    finally:
        _release(fd)
        try:
            os.close(fd)
        except OSError:
            pass
