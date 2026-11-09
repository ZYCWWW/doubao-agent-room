# tools/teach_tool.py
"""授课工具：豆包（老师）把从网上查到、核实过的知识写进本地知识库。

设计意图：模型们自己不联网。上网这件事由豆包代理——查完了以「授课」形式
沉淀进 knowledge_fact，模型们在后续闲聊里通过 _knowledge_inject 自动检索到，
效果上等于老师把外面的知识讲给了他们。

用法（豆包侧调用）：
    teach("知识点正文，一句话说清结论", topic="话题", source="来源URL或站点名")
"""
import time

TEACH_SOURCE = "teacher_web"   # 该来源在 SOURCE_WEIGHT 里没有专门权重，按默认 1.0 参与排序


def teach(text, topic="", source="", session_id=""):
    """写入一条知识。返回给人看的结果说明。"""
    body = (text or "").strip()
    if len(body) < 4:
        return "❌ 知识点太短，没有写入。"
    body = body[:600]
    try:
        from services.knowledge_service import knowledge_service
        meta = {
            "source": TEACH_SOURCE,
            "topic": (topic or "")[:40],
            "origin": (source or "")[:120],
            "heat": 0,
            "ts": time.time(),
            "by": "doubao",
        }
        r = knowledge_service.add_knowledge_safe(body, meta, auto_resolve=True)
    except Exception as e:
        return f"❌ 写入失败：{str(e)[:120]}"

    action = (r or {}).get("action")
    if action == "added":
        return f"✅ 已写入知识库（话题：{topic or '未标'}）：{body[:60]}"
    if action == "skipped":
        return (f"⚠️ 与已有知识冲突，跳过未写入：{(r or {}).get('conflict_detail', '')[:100]}")
    return f"⚠️ 需要人工确认（对齐既有条目）：{(r or {}).get('conflict_detail', '')[:100]}"


def teach_list(top_k=8):
    """列出最近教过的东西，方便确认到底灌了什么进去"""
    try:
        from services.knowledge_service import knowledge_service
        picks = knowledge_service.ranked_search(
            "授课 web 老师", top_k=top_k, threshold=None, cold_reserve=False)
    except Exception as e:
        return f"查询失败：{str(e)[:120]}"
    rows = [p for p in picks if (p.get("source") or "") == TEACH_SOURCE]
    if not rows:
        return "知识库里还没有 teacher_web 来源的条目。"
    lines = [f"teacher_web 共 {len(rows)} 条（仅本次检索范围）："]
    for p in rows:
        lines.append(f"- [{p.get('meta', {}).get('topic') or '-'}] {p['text'][:80]}")
    return "\n".join(lines)


plugin_export = {
    "teach": {"desc": "老师把网上查到的知识写入本地知识库，供模型们检索（传知识点正文）",
              "func": teach},
}
