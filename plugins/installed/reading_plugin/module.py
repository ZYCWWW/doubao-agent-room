# plugins/installed/reading_plugin/module.py
"""阅读能力插件：书库检索 / 读书 / 白名单内目录扫描。

业务本体仍留在 services/book_library.py 与 core/（ACL、config、external_kb），
这里只做工具注册 + 整页面板。

读书的扫描版分支与老版本 tools/book_tool.py 有一处实质区别：
老版本把前几页渲染成 PNG，再让模型去调 vl_analyze_image(...) 看图取正文——
那个工具本身是好的、仍在注册表里（workspace_look 内部也调它），但走法是
「模型再多跑一轮视觉识别」，慢且依赖模型肯不肯调。这里改走
core/external_kb.read(..., ocr=True) 的 OCR 兜底：程序侧一次直接出正文，
扫描版读书更快、更确定。这是优化，不是修死链。
"""
import os
import re
import time

from core.config_loader import config_loader
from core.external_kb import read as kb_read
from core.vision_acl_mgr import vision_acl_mgr
from services.book_library import LIBRARY_PATH, find_book, load_index, scan_library

MAX_TXT = 30000
PDF_PAGE_CAP = 24
PDF_TIME_BUDGET = 40.0
OCR_MAXLEN = 4000
OCR_MIN_TEXT = 40

_OK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffefA-Za-z0-9\s\.\,\-\+\=\%\(\)\[\]\'\"\:\;\/\?\<\>\|\#\&\*\@\_\~\!\^]")
_CID = re.compile(r"\(cid:\d+\)")


# ---------------------------------------------------------------- 正文抽取

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


def _page_count(real: str) -> int:
    try:
        import pymupdf
        doc = pymupdf.open(real)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return 0


def _read_pdf(real: str, b: dict, page=None) -> str:
    """读 PDF：有文字层就抽正文；没有就走 OCR 兜底（不再依赖已下线的视觉工具）。"""
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return (f"《{b['name']}》是 PDF，但当前环境没有 PDF 解析库（PyMuPDF），"
                f"读不了正文。")

    body, total, got, cut = _extract_text(real, PDF_PAGE_CAP, PDF_TIME_BUDGET)
    if body and _ratio_ok(body) >= 0.5:
        note = f"抽自前 {got} 页，全书 {total} 页"
        if cut:
            note += f"（时间到 {int(PDF_TIME_BUDGET)} 秒先停在这里）"
        if len(body) > MAX_TXT:
            body = body[:MAX_TXT]
            note += f"；正文截断在 {MAX_TXT} 字，要看后面就换更具体的书名关键词再点名"
        return f"《{b['name']}》正文（PDF）：\n{body}\n……（{note}）"

    # ---- 扫描版：OCR 兜底 ----
    try:
        pg = int(page) if page else 0
    except Exception:
        pg = 0
    start = max(0, pg - 1) if pg >= 1 else 0
    total3 = total or _page_count(real)
    try:
        t = kb_read(real, pages=1, maxlen=OCR_MAXLEN, start=start, ocr=True)
    except Exception as e:
        t = ""
        err = f"（OCR 出错：{e}）"
    else:
        err = ""
    if t and len(t.strip()) >= OCR_MIN_TEXT:
        where = f"第 {start + 1} 页起" if pg >= 1 else "开头几页"
        return (f"《{b['name']}》扫描版 OCR 正文（{where}，全书 {total3} 页）：\n{t}\n"
                f"……（OCR 逐页识别，可能有错字；要看别处就再传页码，例：book_read(\"{b['name']}\", 页码)）")
    return (f"《{b['name']}》是扫描版（全书 {total3} 页，没有文字层），OCR 也没取出正文{err}。"
            f"换一页再试，或确认这本文件本身能正常打开。")


# ---------------------------------------------------------------- 三个工具

def book_list(query: str = "", session_id: str = ""):
    """查询书库书目，可按书名关键词过滤"""
    idx = load_index()
    hits = idx.get("books") or []
    if query:
        q = query.lower()
        hits = [b for b in hits if q in b["name"].lower() or q in b["rel"].lower()]
    if not hits:
        return (f"书库（{LIBRARY_PATH}）共 {idx.get('count', 0)} 本，"
                f"无匹配（关键词：{query or '空'}）。可用 book_list 不带参数看全部。")
    lines = [f"书库共 {idx.get('count', 0)} 本，以下 {len(hits)} 本："]
    for b in hits[:40]:
        lines.append(f"- {b['rel']}（{b['size'] // 1024}KB）"
                     f"{'✅可读' if b['readable'] else '⚠️不可直接读'}")
    if len(hits) > 40:
        lines.append(f"……还有 {len(hits) - 40} 本，请用更精确的关键词。")
    return "\n".join(lines)


def book_read(name: str, page=None, session_id: str = ""):
    """读取书库中的书，返回正文（txt 直接读，pdf 抽文字层，扫描版走 OCR）。
    page 只对 PDF 有意义，页码 1 起算；注意 page 必须排在 session_id 前面，
    这样模型写 book_read("书名", 20) 时第二个位置参数就是页码。"""
    hits = [b for b in find_book(name) if b["readable"]]
    if not hits:
        return f"未找到可读的书（关键词：{name}）。可用 book_list 查书目。"
    b = hits[0]
    real = os.path.realpath(b["path"])
    lib_real = os.path.realpath(LIBRARY_PATH)
    if not real.startswith(lib_real):
        return "❌ 越界路径，拒绝读取。"
    if not os.path.exists(real):
        return f"《{b['name']}》在书库目录里找不到了（可能被移走或改名），先重新扫描书库。"

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

    return f"《{b['name']}》格式 {b['ext']} 暂不支持直接读。"


def scan_folder(path: str, session_id: str = ""):
    """在白名单内遍历文件夹，列出文件路径（受 ACL 管控）"""
    permit, reason = vision_acl_mgr.check_action_permit(path, "scan_folder")
    if not permit:
        return f"❌ 拒绝扫描：{reason}"
    try:
        max_depth = config_loader["vision_acl"]["max_scan_depth"]
    except Exception:
        max_depth = 3
    out = []
    for root, dirs, files in os.walk(path):
        rel = os.path.relpath(root, path)
        depth = len(rel.split(os.sep))
        if depth > max_depth:
            break
        for f in files:
            out.append(os.path.join(root, f))
    if not out:
        return f"{path} 下没有文件。"
    return "\n".join(out[:100])


plugin_export = {
    "book_list": {
        "desc": "查询书库书目，可按书名关键词过滤",
        "func": book_list,
    },
    "book_read": {
        "desc": "读取书库中的书返回正文（txt 直接读，pdf 抽文字层，扫描版走 OCR；可传页码）",
        "func": book_read,
    },
    "scan_folder": {
        "desc": "在白名单内遍历文件夹文件",
        "func": scan_folder,
    },
}


# ---------------------------------------------------------------- 面板

def panel_render():
    import streamlit as st

    st.header("📖 阅读工作台")
    st.caption("书库检索 → 预览正文 → 扫描版 OCR。业务代码仍在 services/ 与 core/，这里只是入口。")

    idx = load_index()
    books = idx.get("books") or []
    readable = [b for b in books if b["readable"]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("书库总本数", idx.get("count", 0))
    c2.metric("可直接读", len(readable))
    c3.metric("不可直接读", len(books) - len(readable))
    with c4:
        st.caption(f"路径：`{idx.get('path', '')}`")
        st.caption(f"上次扫描：{idx.get('scanned') or '未扫描'}")
        if st.button("🔄 重新扫描书库", key="rd_rescan"):
            with st.spinner("正在扫描书库目录……"):
                new_idx = scan_library()
            if new_idx.get("error"):
                st.error(new_idx["error"])
            else:
                st.success(f"扫描完成，共 {new_idx['count']} 本")
                st.rerun()

    if idx.get("error"):
        st.warning(idx["error"])
    if not books:
        st.info("书库还没有书目。点上面的「重新扫描书库」，或确认书库目录里有文件。")
        return

    st.divider()
    st.subheader("🔍 找书")
    q = st.text_input("书名关键词（留空则列全部）", key="rd_q",
                      placeholder="例如：光学 / 高等数学 / 电路")
    hits = books
    if q:
        ql = q.lower()
        hits = [b for b in books if ql in b["name"].lower() or ql in b["rel"].lower()]
    hits = [b for b in hits if b["readable"]] + [b for b in hits if not b["readable"]]
    if not hits:
        st.info(f"没有匹配「{q}」的书。")
        return

    labels = [f"{'✅' if b['readable'] else '⚠️'} {b['rel']}（{b['size'] // 1024}KB）"
              for b in hits[:200]]
    tip = f"命中 {len(hits)} 本" + ("，只显示前 200 本，请再缩小关键词" if len(hits) > 200 else "")
    st.caption(tip)
    pick = st.selectbox("选择一本", labels, key="rd_pick")
    b = hits[labels.index(pick)]

    st.markdown(f"**{b['name']}**　格式 `{b['ext']}`　大小 {b['size'] // 1024} KB")
    st.caption(f"相对路径：`{b['rel']}`")
    if not b["readable"]:
        st.warning(f"`{b['ext']}` 不在可读格式内（只支持 .pdf / .txt）。")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("📄 预览正文（文字层）", key="rd_read_btn", disabled=not b["readable"]):
            with st.spinner("正在抽取……"):
                if b["ext"] == ".txt":
                    try:
                        with open(b["path"], encoding="utf-8", errors="replace") as f:
                            st.session_state["rd_body"] = f.read()[:3000]
                    except Exception as e:
                        st.session_state["rd_body"] = f"读取失败：{e}"
                else:
                    t = kb_read(b["path"], pages=2, maxlen=3000, ocr=False)
                    st.session_state["rd_body"] = t or "（文字层为空——很可能是扫描版，用右边的 OCR 试读）"
    with col_b:
        if st.button("🔎 OCR 试读第 1 页", key="rd_ocr_btn", disabled=not b["readable"]):
            with st.spinner("OCR 识别中，约 5~8 秒……"):
                t = kb_read(b["path"], pages=1, maxlen=OCR_MAXLEN, start=0, ocr=True)
                st.session_state["rd_body"] = t or "（OCR 也没取出内容，确认这本文件能正常打开）"

    if st.session_state.get("rd_body"):
        st.text_area("正文预览", st.session_state["rd_body"], height=320, key="rd_body_show")
        st.caption("这只是预览，模型侧读书请用工具 `book_read`（支持指定页码）。")

    st.divider()
    st.subheader("🧭 临时扫描其他目录")
    st.caption("受视觉 ACL 白名单管控，只列文件路径、不读内容。书库不走这里，用上面的找书即可。")
    try:
        _wl = vision_acl_mgr.cfg.get("whitelist") or []
    except Exception:
        _wl = []
    if _wl:
        st.caption(f"当前白名单 {len(_wl)} 条：{('、'.join(str(x) for x in _wl[:6]))}"
                   + ("…" if len(_wl) > 6 else ""))
    else:
        st.warning("视觉 ACL 白名单为空，扫描任何路径都会被拒绝。"
                   "先去「🔐 视觉ACL」页把要扫的目录加进白名单。")
    p = st.text_input("目录路径", key="rd_scan_path", placeholder="资料目录")
    if st.button("扫描这个目录", key="rd_scan_btn") and p:
        with st.spinner("遍历中……"):
            r = scan_folder(p)
        st.text_area("扫描结果", r, height=240, key="rd_scan_out")
