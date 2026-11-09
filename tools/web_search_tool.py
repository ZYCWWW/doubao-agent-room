"""联网检索工具：把 core.web_search 的必应检索接到主循环里。

群聊链路（`services/agent_chat._maybe_exec_web`）早就在用同一套检索，
但 ReAct 主循环此前没有任何联网入口，模型只能凭预训练权重作答。
这里只做薄封装：一个工具函数 + plugin_export，注册进 `main.ALL_TOOLS`。

【2026-11-09 迁移】web_search 已搬进 plugins/installed/net_plugin/module.py，
与 browser_fetch 合并成「🌐 联网」插件（工具注册 + 一页面板）。
本文件的函数体保留作回滚参照，main.py 不再从这里 import。
"""
# 迁移说明见上；函数体未改。
from core.logger import root_logger
from core.web_search import search, format_results


def web_search(query: str, limit: int = 3, session_id: str = ""):
    """检索网页，返回「标题 + 一句摘要 + 域名」。

    只给摘要不给正文：本地小模型读不完长文，也容易把原文整段复述出来。
    """
    q = (query or "").strip()
    if not q:
        return "没给检索词，没法联网检索。"
    try:
        n = max(1, min(int(limit or 3), 8))
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


plugin_export = {
    "web_search": {
        "desc": "联网检索网页标题与摘要（必应），需要书库和记忆里没有的现实信息、时事、价格时用；"
                "返回标题+一句摘要+域名，不含全文",
        "func": web_search,
    }
}
