# core/external_kb.py
"""外部资料库：把本地一批教材/手册/标准 PDF 当外部信息源。

2026-11-09 建立，勘查结论写在这（改之前先读）：
- 用户资料盘 G:\\0-Learn 有 2 万个文件 / 22.7GB，其中教材类 PDF 近千本
  （麦克斯韦《电磁通论》、霍罗威茨《电子学》928页、赵凯华《光学》、
   《Fundamentals of Power Electronics》、《电路》第六版、《运算放大器应用技术手册》…）
- 全量 OCR 不现实（22GB、扫描版占多数），所以这里只做
  「元数据索引 + 按需抽页」：索引里只有文件名和路径，正文要用的时候现抽。
- 检索靠文件名/相对路径匹配。中文书名自带主题词（电磁/运算放大器/电路），
  但对抽象课题（"水的沸点与海拔高度的关系"）召回很弱——所以调用方必须接受
  空结果，不能硬凑。

索引文件：<sandbox>/chat_space/external_kb.json（运行时数据，可随时重建）
"""
import json
import os
import re
import time

from core.logger import root_logger

# 索引哪些格式：只有文档类，跳过 .zip/.log/.c/.h 这些占了大头的源码与归档
DOC_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".epub"}
MAX_FILES = 40000        # 防某个盘符异常时把整盘遍历成灾难

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _index_file() -> str:
    return os.path.join(_ROOT, "sandbox", "chat_space", "external_kb.json")


def get_path() -> str:
    """从 config.toml 读外部资料库路径；读不到就返回空串"""
    try:
        from core.config_loader import config_loader
        return str(config_loader["external_kb"]["path"] or "").strip()
    except Exception:
        return ""


def scan(path: str = "", force: bool = False) -> dict:
    """扫描目录、重建索引。path 为空时用配置里的路径。"""
    target = (path or get_path()).strip()
    if not target or not os.path.isdir(target):
        return {"path": target, "count": 0, "books": [], "error": "目录不存在或未配置"}
    books, n = [], 0
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith((".", "$"))]
        for f in files:
            if not f.lower().endswith(tuple(DOC_EXTS)):
                continue
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            books.append({"name": f, "rel": os.path.relpath(p, target),
                          "path": p, "ext": os.path.splitext(f)[1].lower(), "size": size})
            n += 1
            if n >= MAX_FILES:
                break
        if n >= MAX_FILES:
            root_logger.warning(f"[外部库] 超过 {MAX_FILES} 个文档，已截断")
            break
    books.sort(key=lambda b: (-b["size"], b["rel"]))
    idx = {"path": target, "scanned": time.strftime("%Y-%m-%d %H:%M"),
           "count": len(books), "books": books}
    os.makedirs(os.path.dirname(_index_file()), exist_ok=True)
    with open(_index_file(), "w", encoding="utf-8") as fp:
        json.dump(idx, fp, ensure_ascii=False)
    root_logger.info(f"[外部库] 扫描完成 {len(books)} 个文档 @ {target}")
    return idx


def load_index() -> dict:
    """读索引；配置路径变了或索引不存在就重扫"""
    try:
        with open(_index_file(), encoding="utf-8") as fp:
            idx = json.load(fp)
        if idx.get("path") != get_path():
            return scan()
        return idx
    except Exception:
        return scan()


# 中文虚词：切词时先剥掉，否则「的沸」「点与」这种碎片会到处误命中
_STOP = re.compile(r"(的|与|和|及|或|在|是|了|之|其|关系|研究|影响|作用|方法|问题|分析|探讨)")

# 泛词：这类两字词几乎每本书都沾边，单独命中等于是噪音。
# 实测（2026-11-09）：课题「光学系统的成像原理」曾经只靠一个「系统」
# 就命中《MATLAB语言与自动控制系统设计》，正文跟课题毫无关系。
_GENERIC = {"系统", "设计", "方法", "技术", "应用", "分析", "基础", "教程",
            "原理", "理论", "介绍", "手册", "结构", "模型", "计算", "数学"}


def _tokens(query: str) -> list:
    """把课题切成检索片段：英文按词，中文取 2~4 字实词块"""
    q = (query or "").strip().lower()
    if not q:
        return []
    toks = set()
    for w in re.findall(r"[a-z]{2,}", q):
        toks.add(w)
    zh = _STOP.sub(" ", q)
    for seg in re.split(r"[^\u4e00-\u9fff]+", zh):
        if len(seg) < 2:
            continue
        for n in (4, 3, 2):           # 长片段优先，短片段做兜底
            for i in range(0, len(seg) - n + 1):
                # 两字窗口只认词首/词尾：中间切出来的「学系」「像原」是假词，
                # 会靠一个碎片把无关的书抬进结果（实测「光学系统」曾因此召回到
                # 《同济高等数学》——「数学系」里有个「学系」）。
                if n == 2 and not (i == 0 or i == len(seg) - 2):
                    continue
                toks.add(seg[i:i + n])
    # 两字泛词直接丢掉；三四字的还留着，因为它们带方向信息
    return sorted([t for t in toks if not (len(t) <= 2 and t in _GENERIC)],
                  key=len, reverse=True)


def search(query: str, limit: int = 6) -> list:
    """按文件名/相对路径匹配文档。召回弱是常态，无命中就返回 []。"""
    idx = load_index()
    toks = _tokens(query)
    if not toks:
        return []
    scored = []
    for b in idx.get("books") or []:
        name = b["name"].lower()
        rel = b["rel"].lower()
        score = 0.0
        for t in toks:
            # 长片段权重高：命中「运算放大器」比命中「放大」可信得多
            w = 3.0 if len(t) >= 3 else 1.5
            if t in name:
                score += w
            elif t in rel:
                score += w / 3.0
        if score >= 1.5:              # 门槛：一个长词命中文件名，或一个词首/词尾两字词
            scored.append((score, b))
    scored.sort(key=lambda x: (-x[0], -x[1]["size"]))
    return [b for _, b in scored[:limit]]


# ---- 扫描版兜底 ------------------------------------------------------------
# 实测（2026-11-09）：G:\0-Learn 里的大部头教材多是纯扫描版，
# 赫克特《光学》871 页每页 0 字、同济《高等数学》每页只有 61 字水印。
# 所以「抽不到文字层」不等于这本书没用——补一道按需 OCR：
# 只把 1~3 页渲染成图识别，够七步那 240 字用，不做整本识别（871 页要几小时）。
MIN_TEXT = 120            # 文字层少于这个字数就当作无效（水印页常常只有 61 字）
OCR_DPI = 150             # 150dpi 实测中文够认，再高只会更慢
# OCR 采样偏移：不能从第 0 页起——扫描版开头几页是扉页/版权页，识别出来只有书名，
# 会被下游当作「抽不到正文」跳过（实测赵凯华《光学》就这样被误判）。往正文区取。
OCR_OFFSETS = (8, 18, 40)
_OCR_ENGINE = None        # 引擎加载约 2s，进程内只建一次


def _ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _page_text(page) -> str:
    try:
        t = page.get_text() or ""
    except Exception:
        return ""
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _ocr_page(page) -> str:
    """把一页渲染成图再识别；失败返回空串"""
    try:
        import numpy as np
    except ImportError:
        return ""
    try:
        pix = page.get_pixmap(dpi=OCR_DPI)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            arr = arr[:, :, :3]
        res, _ = _ocr_engine()(arr)
        return " ".join(r[1] for r in res if r and len(r) > 1).strip()
    except Exception as e:
        root_logger.warning(f"[外部库] OCR 失败: {e}")
        return ""


def _cache_file(path: str, page_no: int) -> str:
    import hashlib
    key = hashlib.md5(f"{path}|{page_no}|{OCR_DPI}".encode("utf-8")).hexdigest()
    d = os.path.join(os.path.dirname(_index_file()), "ocr_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, key + ".txt")


def _ocr_best(doc, start: int, offsets, pdf_path: str) -> str:
    """在若干正文页采样 OCR，取结果最长的一页；带磁盘缓存"""
    best = ""
    total = len(doc)
    pages = []
    for off in offsets:
        i = start + off
        if 0 <= i < total:
            pages.append(i)
    for i in pages:
        cf = _cache_file(pdf_path, i)
        if os.path.isfile(cf):
            try:
                with open(cf, encoding="utf-8") as f:
                    t = f.read().strip()
            except Exception:
                t = ""
        else:
            t = _ocr_page(doc[i])
            try:                      # 空结果也缓存，免得下次再白跑一遍
                with open(cf, "w", encoding="utf-8") as f:
                    f.write(t)
            except Exception:
                pass
        if len(t) > len(best):
            best = t
    return best


def read(path: str, pages: int = 2, maxlen: int = 600, start: int = 0,
         ocr: bool = True) -> str:
    """抽一本书的正文。文字层优先，抽不到就按需 OCR 兜底。
    只取一小段、截 maxlen 字——这块要塞进 prompt，不能贪多。"""
    if not path or not os.path.isfile(path):
        return ""
    if not path.lower().endswith(".pdf"):
        if path.lower().endswith((".txt", ".md")):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    return f.read()[:maxlen]
            except Exception:
                return ""
        return ""
    try:
        import pymupdf
    except ImportError:
        return ""
    try:
        doc = pymupdf.open(path)
    except Exception:
        return ""
    try:
        total = len(doc)
        # 1) 文字层：在 [start, start+pages*3) 里挑最长的一段，避开封面/目录空页
        best = ""
        for i in range(start, min(total, start + max(pages, pages * 3))):
            t = _page_text(doc[i])
            if len(t) > len(best):
                best = t
        if len(best) >= MIN_TEXT:
            return best[:maxlen]
        # 2) 扫描版：OCR 兜底
        if ocr:
            t = _ocr_best(doc, start, OCR_OFFSETS, path)
            if t:
                return t[:maxlen]
        return best[:maxlen]          # 有零星字也比空好，但调用方能看出它很弱
    finally:
        doc.close()


def stats() -> dict:
    """给面板用：条数 / 总体积 / 路径 / 扫描时间"""
    idx = load_index()
    total = sum(b.get("size", 0) for b in idx.get("books") or [])
    return {"path": idx.get("path", ""), "count": idx.get("count", 0),
            "gb": round(total / 1073741824, 2), "scanned": idx.get("scanned", ""),
            "error": idx.get("error", "")}
