# core/gb_std.py
"""国家标准题录检索（openstd.samr.gov.cn，无需 key、无需登录）

2026-11-09 实测结论，改之前先读：
- 检索：GET /bzgk/gb/std_list?p.p1=0&p.p90=circulation_date&p.p91=desc&p.p2=<关键词>
  返回 HTML 列表，无验证码、无 cookie 要求，每行 onclick="showInfo('<hcno>')"。
- 全文：/bzgk/gb/showGb?type=online&hcno=<hcno> 是**逐页图片**（pdfImg-1..N），
  要拿到文字必须 OCR；type=download 还要输验证码。
  所以这里只做「题录」——能告诉七步「这个课题有哪些国标在测这个量」，
  给不了具体数值。别再指望从这儿直接抠出数字。

用法：
    from core.gb_std import search_standards
    rows = search_standards("沸点")
    # -> [{"code","name","status","type","issue","implement","hcno"}]
"""
import re
from html import unescape

import requests

from core.logger import root_logger

_LIST_URL = "https://openstd.samr.gov.cn/bzgk/gb/std_list"
_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 一行是一条标准的 <tr>：里面既有 onclick="showInfo('hcno')"，也有标准号和名称
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_HCNO = re.compile(r"showInfo\('([0-9A-Fa-f]+)'\)")
_CODE = re.compile(r"(GB(?:/T|/Z)?\s*\d+(?:\.\d+)*[-—]\d{4})")


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def search_standards(keyword: str, limit: int = 8, timeout: float = 12.0) -> list:
    """按关键词查国标题录，返回结构化行；失败返回 []（不抛异常）"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    try:
        r = requests.get(_LIST_URL,
                         params={"p.p1": 0, "p.p90": "circulation_date", "p.p91": "desc",
                                 "p.p2": keyword},
                         headers=_UA, timeout=timeout)
        if r.status_code != 200:
            root_logger.warning(f"[国标] 「{keyword}」HTTP {r.status_code}")
            return []
        r.encoding = r.apparent_encoding or "utf-8"
        page = r.text
    except Exception as e:
        root_logger.warning(f"[国标] 「{keyword}」失败: {str(e)[:100]}")
        return []

    rows = []
    for block in _ROW.findall(page):
        mh = _HCNO.search(block)
        mc = _CODE.search(_plain(block))
        if not mh or not mc:
            continue
        cells = [_plain(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)]
        # 列序：序号 | 标准号 | 采标情况 | 名称 | 类型 | 状态 | 发布日期 | 实施日期 | 操作
        name = ""
        for c in cells:
            if c and not _CODE.search(c) and not c.isdigit() and len(c) > 4:
                name = c
                break
        status = next((c for c in cells if c in ("现行", "废止", "即将实施")), "")
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", block)
        rows.append({
            "code": mc.group(1),
            "name": name[:60],
            "status": status,
            "type": next((c for c in cells if c in ("推标", "强标", "国标")), ""),
            "issue": dates[0] if dates else "",
            "implement": dates[1] if len(dates) > 1 else "",
            "hcno": mh.group(1),
        })
        if len(rows) >= limit:
            break
    root_logger.info(f"[国标] 「{keyword}」命中 {len(rows)} 条题录")
    return rows


def format_standards(rows: list, limit: int = 4) -> str:
    """拼成给 prompt 看的紧凑清单（只有题录，明确不含正文数值）"""
    if not rows:
        return ""
    out = []
    for i, r in enumerate(rows[:limit], 1):
        seg = f"[{i}] {r['code']} {r['name']}"
        tags = [t for t in (r.get("status"), r.get("issue")) if t]
        if tags:
            seg += "（" + "，".join(tags) + "）"
        out.append(seg)
    return "\n".join(out)
