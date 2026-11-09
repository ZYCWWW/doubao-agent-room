"""联网检索：给本地模型一只真正能查到东西的手。

后端 cn.bing.com（国内直连实测 200 / 130KB / 10 条结果，无需 API key）。
只取标题 + 摘要 + 域名，不做全文抓取——小模型读不完长文，也容易把原文整段复述。
"""
import re
from html import unescape
from urllib.parse import urlparse

import requests

from core.logger import root_logger

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _plain(s: str) -> str:
    """去标签 + 反转义 + 压空白"""
    s = unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def search(query: str, limit: int = 5, timeout: float = 12.0) -> list:
    """检索网页，返回 [{"title","url","snippet"}]；网络/解析失败返回 []"""
    query = (query or "").strip()
    if not query:
        return []
    try:
        r = requests.get("https://cn.bing.com/search", params={"q": query},
                         headers=_UA, timeout=timeout)
        if r.status_code != 200:
            root_logger.warning(f"[联网检索] 「{query}」HTTP {r.status_code}")
            return []
        page = r.text
    except Exception as e:
        root_logger.warning(f"[联网检索] 「{query}」失败: {str(e)[:100]}")
        return []

    items = []
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S)[:limit]:
        m = re.search(r'<h2.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        title = _plain(m.group(2))
        if not title:
            continue
        sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        items.append({
            "title": title[:80],
            "url": unescape(m.group(1)),
            "snippet": _plain(sm.group(1))[:160] if sm else "",
        })
    root_logger.info(f"[联网检索] 「{query}」命中 {len(items)} 条")
    return items


def format_results(items: list, limit: int = 3) -> str:
    """拼成群里看的紧凑清单（标题 + 一句摘要 + 域名），控制长度防复述"""
    if not items:
        return "没搜到条目。"
    out = []
    for i, it in enumerate(items[:limit], 1):
        snip = it["snippet"]
        out.append(f"[{i}] {it['title']}（{domain_of(it['url'])}）" + (f"\n    {snip}" if snip else ""))
    return "\n".join(out)
