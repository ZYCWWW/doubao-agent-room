# tools/book_tool.py
"""读书工具：书库查询与阅读
- 文字版 PDF / txt：直接抽正文给模型
- 扫描版 PDF：把前几页渲染成图片，让模型用 vl_analyze_image 读页（不假装读过）
解析用 PyMuPDF：比 pdfplumber 快 3~6 倍，且不会吐 (cid:xxxx) 乱码。
安全：只读书库目录内的文件（realpath 前缀校验，防路径穿越）
"""
# ⚠️ 2026-11-09 起本文件不再由 main.py 注册：book_list / book_read 已迁到
#    plugins/installed/reading_plugin。这里留一版便于回滚，不要在此继续加功能。
import os
import re
import time
from services.book_library import find_book, load_index, LIBRARY_PATH

MAX_TXT = 30000
PDF_PAGE_CAP = 24        # 单次最多抽前 24 页：够开启讨论，20MB 的书也不至于卡住整轮接龙
PDF_TIME_BUDGET = 40.0   # 抽文字的总时限（秒）；超时就用已抽到的部分
SCAN_RENDER_PAGES = 3    # 扫描版先渲染前 3 页给视觉工具，够判断内容了

# 判定「这段是不是正常中文」：中日韩、全角标点、拉丁字母、数字、常见符号
_OK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffefA-Za-z0-9\s\.\,\-\+\=\%\(\)\[\]\'\"\:\;\/\?\<\>\|\#\&\*\@\_\~\!\^]")
_CID = re.compile(r"\(cid:\d+\)")

_PAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "sandbox", "chat_space", "book_pages")


def _clean(text: str) -> str:
    text = _CID.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ratio_ok(text: str) -> float:
    if not text:
        return 0.0
    return len(_OK.findall(text)) / len(text)


def _extract_text(real: str, cap: int, budget: float):
    """抽文字层。返回 (正文, 总页数, 已抽页数, 是否因超时中断)"""
    import pymupdf
    t0 = time.time()
    parts = []
    cut = False
    doc = pymupdf.open(real)
    try:
        total = len(doc)
        for i in range(min(total, cap)):
            if time.time() - t0 > budget:
                cut = True
                break
            try:
                t = _clean(doc[i].get_text() or "")
            except Exception:
                t = ""
            if t:
                parts.append(t)
    finally:
        doc.close()
    return "\n".join(parts).strip(), total, len(parts), cut


def _render_pages(real: str, name: str, pages: int, start: int = 0):
    """扫描版：渲染成图，交给视觉工具读。start 从 0 起算（0 = 前几页）。
    同理重复渲染同一页是幂等的，模型反复点名也不会堆垃圾文件。"""
    import pymupdf
    os.makedirs(_PAGE_DIR, exist_ok=True)
    stem = re.sub(r"[^\w\u4e00-\u9fff]+", "_", os.path.splitext(name)[0])[:40] or "book"
    doc = pymupdf.open(real)
    paths = []
    total = 0
    try:
        total = len(doc)
        lo = max(0, start)
        hi = min(total, start + pages)
        for i in range(lo, hi):
            try:
                pix = doc[i].get_pixmap(dpi=140)
                p = os.path.join(_PAGE_DIR, f"{stem}_p{i + 1}.png")
                pix.save(p)
                if os.path.exists(p) and os.path.getsize(p) > 1024:
                    paths.append((i + 1, p))
            except Exception:
                continue
    finally:
        doc.close()
    return paths, total


def _read_pdf(real: str, b: dict, page=None) -> str:
    """读 PDF：有文字层就抽正文，没有就转图片让模型自己看。
    page 指定页码（1 起算）时，只渲染那一页和前后各一页，专供扫描版翻正文用。"""
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return (f"《{b['name']}》是 PDF，但当前环境没有 PDF 解析库（PyMuPDF），"
                f"读不了正文。请老师协助。")

    body, total, got, cut = _extract_text(real, PDF_PAGE_CAP, PDF_TIME_BUDGET)
    ratio = _ratio_ok(body)

    if not body or ratio < 0.5:
        # 指定页码：渲染该页 ±1，让模型能一页一页往后翻
        if page:
            try:
                pg = int(page)
            except Exception:
                pg = 0
            if pg >= 1:
                paths, total2 = _render_pages(real, b["name"], SCAN_RENDER_PAGES, start=pg - 2)
                if not paths:
                    return (f"《{b['name']}》第 {pg} 页渲染失败（全书 {total2 or total} 页），"
                            f"换一页试，或看看书是不是被移走了。")
                got_pages = [n for n, _ in paths]
                rng = f"第 {got_pages[0]}~{got_pages[-1]} 页" if len(got_pages) > 1 else f"第 {got_pages[0]} 页"
                total3 = total2 or total
                lines = [f"《{b['name']}》{rng}已渲染成图（全书 {total3} 页）："]
                for n, p in paths:
                    lines.append(f'vl_analyze_image("{p}", "这一页写了什么")')
                lines.append(f"看完这几页再说内容。要往后翻，写 book_read(\"{b['name']}\", 页码) 换页。")
                return "\n".join(lines)

        paths, total2 = _render_pages(real, b["name"], SCAN_RENDER_PAGES)
        if not paths:
            return (f"《{b['name']}》是 PDF（{total2 or total} 页），既抽不出文字、"
                    f"页面也渲染失败。这本书暂时读不了。")
        total3 = total2 or total
        lines = [f"《{b['name']}》是扫描版（{total3} 页，整页是图片，没有文字层）。",
                 f"前 {len(paths)} 页已渲染成图，通常只是封面/版权/目录，里头没有正文。",
                 f"想看正文，直接写 book_read(\"{b['name']}\", 页码) ，一次给你那一页和前后各一页。",
                 "在真正看到那一页之前，不要猜书里写了什么。"]
        for n, p in paths:
            lines.append(f'vl_analyze_image("{p}", "这一页写了什么")')
        return "\n".join(lines)

    note = f"抽自前 {got} 页，全书 {total} 页"
    if cut:
        note += f"（时间到 {int(PDF_TIME_BUDGET)} 秒先停在这里）"
    if len(body) > MAX_TXT:
        body = body[:MAX_TXT]
        note += f"；正文截断在 {MAX_TXT} 字，要看后面就换更具体的书名关键词再点名"
    return f"《{b['name']}》正文（PDF）：\n{body}\n……（{note}）"


def book_list(query: str = "", session_id: str = ""):
    """查询老师书库书目，可按书名关键词过滤"""
    idx = load_index()
    hits = idx["books"]
    if query:
        q = query.lower()
        hits = [b for b in hits if q in b["name"].lower() or q in b["rel"].lower()]
    if not hits:
        return f"书库（{LIBRARY_PATH}）共 {idx['count']} 本，无匹配（关键词：{query or '空'}）。可用 book_list 不带参数看全部。"
    lines = [f"书库共 {idx['count']} 本，以下 {len(hits)} 本："]
    for b in hits[:40]:
        lines.append(f"- {b['rel']}（{b['size'] // 1024}KB）{'✅可读' if b['readable'] else '⚠️不可直接读'}")
    if len(hits) > 40:
        lines.append(f"……还有 {len(hits) - 40} 本，请用更精确的关键词。")
    return "\n".join(lines)


def book_read(name: str, page=None, session_id: str = ""):
    """读取书库中的书：txt 与 pdf 都返回正文（按书名关键词定位）。
    page 只对扫描版 PDF 有效：指定页码（1 起算）时渲染那一页和前后各一页，
    让模型能一页一页往后翻，而不是卡在封面页说"等正文"。
    注意 page 必须排在 session_id 前面——模型写 book_read("书名", 20) 时第二个位置参数就是页码。"""
    hits = [b for b in find_book(name) if b["readable"]]
    if not hits:
        return f"未找到可读的书（关键词：{name}）。可用 book_list 查书目。"
    b = hits[0]
    real = os.path.realpath(b["path"])
    lib_real = os.path.realpath(LIBRARY_PATH)
    if not real.startswith(lib_real):
        return "❌ 越界路径，拒绝读取。"
    if not os.path.exists(real):
        return f"《{b['name']}》在书库目录里找不到了（可能被移走或改名），先让老师重新扫描书库。"
    if b["ext"] == ".txt":
        try:
            with open(real, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return f"读取失败：{e}"
        if len(content) > MAX_TXT:
            head = content[:MAX_TXT]
            return f"《{b['name']}》正文前段（全文 {len(content)} 字符，先给前 {MAX_TXT}）：\n{head}\n……（需要后续段落再问）"
        return f"《{b['name']}》正文：\n{content}"
    if b["ext"] == ".pdf":
        return _read_pdf(real, b, page=page)
    return f"《{b['name']}》格式 {b['ext']} 暂不支持直接读，可请老师协助。"


plugin_export = {
    "book_list": {"desc": "查询老师书库书目，可按书名关键词过滤", "func": book_list},
    "book_read": {"desc": "读取书库中的书，返回正文（txt 直接读，pdf 抽文字层，扫描版转图片，传书名关键词）", "func": book_read},
}
