# services/book_library.py
"""书库服务：本地电子书目录（路径由 config 的 [library] path 决定，默认项目内 books/）
- scan_library(): 全盘扫描生成持久化书目索引 sandbox/chat_space/library.json
- list_books()/find_book(kw): 查询书目、按关键词定位文件路径（供豆包 Read 读书）
- 可读格式：pdf/txt（chm/eddx 为不可直接阅读格式，标注但不纳入读书入口）
"""
import json
import os
import time

from core.config_loader import config_loader

LIBRARY_PATH = (config_loader.get("library", {}) or {}).get("path") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "books")
INDEX_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "sandbox", "chat_space", "library.json")
READABLE_EXTS = {".pdf", ".txt"}


def scan_library() -> dict:
    """扫描书库目录，重建索引（新增书籍后调用）"""
    books = []
    if not os.path.isdir(LIBRARY_PATH):
        return {"path": LIBRARY_PATH, "scanned": "", "count": 0, "books": [], "error": "书库目录不存在"}
    for root, _dirs, files in os.walk(LIBRARY_PATH):
        for f in sorted(files):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, LIBRARY_PATH)
            ext = os.path.splitext(f)[1].lower()
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            books.append({"name": f, "rel": rel, "path": p, "ext": ext,
                          "readable": ext in READABLE_EXTS, "size": size})
    idx = {"path": LIBRARY_PATH,
           "scanned": time.strftime("%Y-%m-%d %H:%M"),
           "count": len(books),
           "books": books}
    os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as fp:
        json.dump(idx, fp, ensure_ascii=False, indent=2)
    return idx


def load_index(validate: bool = True) -> dict:
    """读索引；索引里失效条目过多时自动重扫。
    索引是持久化快照——书被移走或改名后它会变旧，直接读会让模型看到一堆幽灵书、
    book_read 再报「文件不存在」。所以按失效比例判断要不要重扫。"""
    try:
        with open(INDEX_FILE, encoding="utf-8") as fp:
            idx = json.load(fp)
        if validate:
            books = idx.get("books") or []
            missing = 0
            for b in books:
                if not os.path.exists(b.get("path", "")):
                    missing += 1
            # 两本以内算正常增删（重扫有成本），超过 20% 就认为索引已过期
            if missing > 2 and missing > len(books) * 0.2:
                return scan_library()
        return idx
    except Exception:
        return scan_library()


def list_books() -> dict:
    return load_index()


def find_book(kw: str) -> list:
    """按书名/路径关键词定位书籍，返回匹配条目列表（readable 优先）"""
    idx = load_index()
    kw_l = kw.lower()
    hits = [b for b in idx["books"] if kw_l in b["name"].lower() or kw_l in b["rel"].lower()]
    hits.sort(key=lambda b: (not b["readable"], b["name"]))
    return hits


def summarize_catalog() -> str:
    """生成给模型们看的书库上架播报（分类目录，只列可读格式）"""
    idx = load_index()
    lines = [f"📚 老师的书库（{idx['path']}）共 {idx['count']} 本，书架如下："]
    # 按目录分类：根目录文件归入顶层；子目录按相对路径前缀分组
    groups = {}
    for b in idx["books"]:
        if not b["readable"]:
            continue
        head = os.path.dirname(b["rel"])
        head = head if head else "· 书架顶层"
        groups.setdefault(head, []).append(b["name"])
    for head in sorted(groups, key=lambda x: (x == "· 书架顶层", x)):
        names = "、".join(groups[head])
        lines.append(f"【{head}】{names}")
    lines.append("想读哪本直接点名（例：'想听《XXX》'），豆包老师会读给你们讲。")
    return "\n".join(lines)