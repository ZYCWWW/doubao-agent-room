# plugins/installed/net_plugin/module.py
"""联网插件：必应检索 + 网页正文抓取。

业务本体一行没动：检索仍在 core/web_search.py（requests 抓 cn.bing.com +
正则解析 b_algo 块），抓取就是一次 requests.get。这里只是把原来
tools/web_search_tool.py 与 tools/browser_tool.py 里的两个薄封装搬过来，
再补一页面板，让检索/抓取在页面上也能手动跑。

两个工具的函数体逐字照搬，没有改行为。
"""
import requests
import streamlit as st

from core.logger import root_logger
from core.web_search import search, format_results, domain_of

_SEARCH_CAP = 8      # 与工具侧 limit 上限对齐
_FETCH_CAP = 4000    # 与工具侧正文截断长度对齐


# ---------------------------------------------------------------- 两个工具

def web_search(query: str, limit: int = 3, session_id: str = ""):
    """检索网页，返回「标题 + 一句摘要 + 域名」。

    只给摘要不给正文：本地小模型读不完长文，也容易把原文整段复述出来。
    """
    q = (query or "").strip()
    if not q:
        return "没给检索词，没法联网检索。"
    try:
        n = max(1, min(int(limit or 3), _SEARCH_CAP))
    except (TypeError, ValueError):
        n = 3
    try:
        items = search(q, limit=n)
    except Exception as e:
        root_logger.warning(f"[联网检索] 工具调用异常: {str(e)[:80]}")
        return f"联网检索失败：{str(e)[:120]}"
    if not items:
        return f"【联网检索】{q}\n没搜到条目（网络不通或关键词太窄），换个说法再试。"
    return (f"【联网检索】{q}\n{format_results(items, limit=n)}\n"
            f"（以上只是检索摘要，具体细节要核实请自己再查；别把没出现过的结论当成搜到的）")


def browser_fetch(url: str):
    """网页文本抓取（简单 GET，只取正文前 4000 字符）"""
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.text[:_FETCH_CAP]
    except Exception as e:
        root_logger.error(f"网页抓取失败 {e}")
        return f"fetch fail: {e}"


plugin_export = {
    "web_search": {
        "desc": "联网检索网页标题与摘要（必应），需要书库和记忆里没有的现实信息、时事、价格时用；"
                "返回标题+一句摘要+域名，不含全文",
        "func": web_search,
    },
    "browser_fetch": {
        "desc": "网页文本抓取",
        "func": browser_fetch,
    },
}


# ---------------------------------------------------------------- 整页面板
#
# 注意：这一版 Streamlit 对「一行里既有 inline 格式又有中文标点」处理不稳，
# 一个 list item 会被拆成两行渲染（space_plugin 那次踩过）。所以下面所有
# 内容行一律纯文本，不套反引号也不套加粗。

def panel_render():
    import time as _t

    st.header("🌐 联网")
    st.caption("必应检索 + 网页正文抓取。业务本体在 core/web_search.py，这里只做入口和记录。")

    hist = st.session_state.setdefault("np_hist", [])

    st.caption("本次会话：检索 " + str(len(hist)) + " 次")

    t_search, t_fetch = st.tabs(["🔍 检索", "📥 抓取"])

    with t_search:
        q = st.text_input("检索词", key="np_q", placeholder="例如：2026 年国产 GPU 进展")
        n = st.selectbox("取几条", [1, 2, 3, 4, 5, 6, 7, 8], index=2, key="np_n")
        if st.button("🔍 检索", key="np_search_btn"):
            qq = (q or "").strip()
            if not qq:
                st.warning("先填检索词")
            else:
                try:
                    items = search(qq, limit=int(n))
                except Exception as e:
                    items = []
                    st.error("检索异常：" + str(e)[:120])
                hist.insert(0, {"q": qq, "n": int(n), "items": items,
                                "ts": _t.strftime("%H:%M:%S", _t.localtime())})
                del hist[10:]

        if hist:
            cur = hist[0]
            st.write("最近一次：" + cur["q"] + "（" + cur["ts"] + "，命中 "
                     + str(len(cur["items"])) + " 条）")
            if not cur["items"]:
                st.info("没搜到条目。网络不通或关键词太窄，换个说法再试。")
            for i, it in enumerate(cur["items"], 1):
                st.write(str(i) + ". " + it["title"] + "　[" + domain_of(it["url"]) + "]")
                if it.get("snippet"):
                    st.caption("　" + it["snippet"])
                st.caption("　" + it["url"])
            if len(hist) > 1:
                st.divider()
                st.write("更早的检索")
                for h in hist[1:]:
                    st.caption("· " + h["ts"] + "　" + h["q"] + "　命中 "
                               + str(len(h["items"])) + " 条")
        else:
            st.info("还没有检索记录。填个词点上面的按钮试试。")

    with t_fetch:
        url = st.text_input("网页地址", key="np_url", placeholder="https://...")
        if st.button("📥 抓取", key="np_fetch_btn"):
            uu = (url or "").strip()
            if not uu:
                st.warning("先填网址")
            elif not uu.lower().startswith(("http://", "https://")):
                st.warning("网址要以 http:// 或 https:// 开头")
            else:
                try:
                    r = requests.get(uu, timeout=30)
                    r.raise_for_status()
                    body = r.text
                    st.session_state["np_body"] = {
                        "url": uu, "status": r.status_code,
                        "len": len(body), "head": body[:_FETCH_CAP],
                    }
                except Exception as e:
                    st.session_state["np_body"] = {"url": uu, "error": str(e)[:200]}

        body = st.session_state.get("np_body")
        if body:
            if body.get("error"):
                st.error("抓取失败：" + body["error"])
            else:
                st.write("HTTP " + str(body["status"]) + "　全文 " + str(body["len"])
                         + " 字符，下面只显示前 " + str(_FETCH_CAP) + " 个")
                st.caption(body["url"])
                st.text_area("正文（原样，含 HTML 标签）", body.get("head", ""),
                             height=420, key="np_body_show")
        else:
            st.info("还没有抓取。填个网址点上面的按钮试试。")
            st.caption("提示：只取正文前 4000 字符、不做正文抽取；页面若是纯 JS 渲染可能抓不到内容。")
