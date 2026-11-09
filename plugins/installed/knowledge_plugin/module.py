"""
知识库插件（📚 知识库，order 90）

【2026-11-09 迁移】原 dashboard.py 页 4（`if _page == 4:`）的检索 + 待仲裁队列
整段搬到这里，并补上 P0-2 的「编辑 / 删除 / 设为永久」三项能力。

本插件**不导出任何工具**：知识库的读写本来就由 main.py 主循环、skill_extractor、
research/pipeline、tutor_service、teach_tool 直接调 services.knowledge_service，
从来没有注册成模型可调用工具。所以 plugin_export 是空 dict。

业务本体一行未动：core/chroma_kb.py（只新增了 delete_document，原接口不变）与
services/knowledge_service.py 保持原地。

────────────────────────────────────────────────────────────────────────────
关于「设为永久」的一个诚实说明
────────────────────────────────────────────────────────────────────────────
`is_permanent` 只有一处消费者：services/memory_service.py 的 scan_and_archive，
它只遍历 memory_core / memory_cold，遇到 is_permanent 就跳过下沉。

而 knowledge_fact / knowledge_skill / knowledge_rule / episode_memory 这四个集合
**不参与自动归档**（episode_memory 还明确「不再作为写入/归档目标」）。所以在这四个
集合上写 is_permanent 是没人读的死标记 —— 不做这种假功能。

本页只在分区记忆集合（memory_core / memory_cold）的结果上给「设为永久」按钮，
其余集合不显示该按钮，并用 caption 说明原因。

────────────────────────────────────────────────────────────────────────────
Streamlit 时序约定（沿用 debate_plugin 的结论）
────────────────────────────────────────────────────────────────────────────
写在按钮里的数据，同一帧读不到（按钮在页面下半部、结果区在下半部也算，只要写入
发生在读取之后）。所以凡是有写入的分支，一律：
    写 st.session_state["kp_msg"] → st.rerun()
结果集也存 session_state（kp_hits），rerun 后再渲染。
"""
import time

import streamlit as st

from core.chroma_kb import chroma_kb
from core.logger import root_logger
from services.knowledge_service import knowledge_service

# 纯面板插件：知识库没有对模型暴露的工具，这里刻意留空
plugin_export = {}

_STATUS_CN = {"conflict": "冲突", "complement": "互补"}

# 只有这两个集合会被 scan_and_archive 遍历，is_permanent 才有意义
_PERMANENT_COLLS = ("memory_core", "memory_cold")

# 展示顺序：四个旧集合在前（模型实际写入的），分区记忆在后
_ORDER = ("knowledge_fact", "knowledge_skill", "knowledge_rule", "episode_memory",
          "memory_core", "memory_cold", "memory_archive", "reflection")


def _coll_names() -> list:
    """实际已打开的集合名。分区集合只在面板进程创建过才存在。"""
    try:
        # 先刷新：别的进程刚写入的条目不刷新看不到（见 ChromaKB.refresh_collections）
        chroma_kb.refresh_collections()
        have = list(chroma_kb.collections.keys())
    except Exception as e:
        root_logger.error("知识库集合读取失败：%s" % str(e)[:120])
        return list(_ORDER[:4])
    ordered = [c for c in _ORDER if c in have]
    return ordered + [c for c in have if c not in _ORDER]


def _count(name: str) -> int:
    try:
        return int(chroma_kb.collections[name].count())
    except Exception:
        return -1


def _brief_meta(meta) -> str:
    """metadata 压成一行可读文本；chroma 只存标量，嵌套值直接跳过"""
    if not isinstance(meta, dict) or not meta:
        return "（无元数据）"
    parts = []
    for k in sorted(meta.keys()):
        v = meta.get(k)
        if not isinstance(v, (str, int, float, bool)):
            continue
        s = str(v)
        if len(s) > 60:
            s = s[:60] + "…"
        parts.append(k + "=" + s)
    return "　".join(parts) if parts else "（无元数据）"


def panel_render():
    st.header("📚 知识库")
    st.caption(
        "四个知识集合 + 分区记忆。检索走 services.knowledge_service，"
        "向量库本体在 core/chroma_kb.py，这里只做页面。"
    )

    # 一次性消息：跨 rerun 传递，显示完即弹出
    _msg = st.session_state.pop("kp_msg", None)
    if _msg:
        st.success(_msg)

    colls = _coll_names()
    counts = []
    for c in colls:
        n = _count(c)
        counts.append(c + "：" + ("?" if n < 0 else str(n)))
    st.write("条数　" + "　".join(counts))
    st.divider()

    # ---------- 检索 ----------
    st.subheader("🔍 检索")
    _q = st.text_input("检索关键词", key="kp_q")
    _coll = st.selectbox("知识库集合", colls, key="kp_coll")
    if st.button("检索", key="kp_btn"):
        if not _q.strip():
            st.session_state["kp_hits"] = {"coll": _coll, "items": [],
                                           "query": _q, "err": "检索词不能为空"}
            st.rerun()
        else:
            try:
                hits = knowledge_service.search_knowledge(_q, top_k=5, coll=_coll)
                st.session_state["kp_hits"] = {"coll": _coll, "items": hits or [],
                                               "query": _q, "err": ""}
            except Exception as e:
                root_logger.error("知识库检索失败：%s" % str(e)[:200])
                st.session_state["kp_hits"] = {"coll": _coll, "items": [],
                                               "query": _q, "err": str(e)[:200]}
            st.rerun()

    # 结果区在检索控件之后，读的是 rerun 后已写入的 kp_hits
    _h = st.session_state.get("kp_hits")
    if _h:
        if _h.get("err"):
            st.warning(_h["err"])
        elif not _h.get("items"):
            st.info("「" + str(_h.get("query", "")) + "」在 " +
                    str(_h.get("coll", "")) + " 里没有命中。")
        else:
            st.caption("集合 " + str(_h.get("coll", "")) + "　命中 " +
                       str(len(_h["items"])) + " 条　检索词：" +
                       str(_h.get("query", "")))
            _hc = _h["coll"]
            for _i, _it in enumerate(_h["items"]):
                # 用 st.text 而不是 st.write：st.write 走 markdown，长 hex id 与全角
                # 空格混排会被前端拆成两行重复渲染（浏览器实测，AppTest 看不出来）
                st.text(str(_i + 1) + ". id: " + str(_it.get("id")) +
                        "　distance=" + ("%.3f" % float(_it.get("distance") or 0.0)))
                st.text(str(_it.get("document") or ""))
                st.caption(_brief_meta(_it.get("metadata")))
                st.caption("集合：" + _hc)

                _b1, _b2, _b3 = st.columns(3)
                _editing = st.session_state.get("kp_edit_idx") == _i
                if _b1.button("✏️ 编辑" if not _editing else "收起义",
                              key="kp_edit_" + str(_i)):
                    st.session_state["kp_edit_idx"] = None if _editing else _i
                    st.rerun()

                if _b2.button("🗑 删除", key="kp_del_" + str(_i)):
                    st.session_state["kp_del_idx"] = _i
                    st.rerun()

                _can_perm = _hc in _PERMANENT_COLLS
                if _can_perm:
                    if _b3.button("♾ 设为永久", key="kp_perm_" + str(_i)):
                        _meta = dict(_it.get("metadata") or {})
                        _meta["is_permanent"] = True
                        try:
                            chroma_kb.update_document(
                                _hc, str(_it.get("id")),
                                str(_it.get("document") or ""), _meta)
                            _it["metadata"] = _meta
                            st.session_state["kp_hits"] = _h
                            st.session_state["kp_msg"] = (
                                "已设为永久：" + str(_it.get("id")) +
                                "（scan_and_archive 将跳过它）")
                        except Exception as e:
                            root_logger.error("设为永久失败：%s" % str(e)[:200])
                            st.session_state["kp_msg"] = "设为永久失败：" + str(e)[:120]
                        st.rerun()

                # 编辑面板
                if _editing:
                    _new = st.text_area("修改后的正文", value=str(_it.get("document") or ""),
                                        key="kp_ta_" + str(_i), height=160)
                    if st.button("💾 保存修改", key="kp_save_" + str(_i)):
                        _meta = dict(_it.get("metadata") or {})
                        _meta["edited_ts"] = time.time()
                        try:
                            chroma_kb.update_document(_hc, str(_it.get("id")),
                                                      _new, _meta)
                            _it["document"] = _new
                            _it["metadata"] = _meta
                            st.session_state["kp_hits"] = _h
                            st.session_state["kp_msg"] = (
                                "已保存：" + str(_it.get("id")) + "（固定 " +
                                str(len(_new)) + " 字）")
                            st.session_state["kp_edit_idx"] = None
                        except Exception as e:
                            root_logger.error("知识库编辑保存失败：%s" % str(e)[:200])
                            st.session_state["kp_msg"] = "保存失败：" + str(e)[:120]
                        st.rerun()

                # 删除二次确认（破坏性操作不一次点掉）
                if st.session_state.get("kp_del_idx") == _i:
                    st.warning("确认删除这条？删除后无法恢复。id: " + str(_it.get("id")))
                    _d1, _d2, _d3 = st.columns(3)
                    if _d1.button("确认删除", key="kp_del_ok_" + str(_i)):
                        try:
                            chroma_kb.delete_document(_hc, str(_it.get("id")))
                            _items = list(_h["items"])
                            _items.pop(_i)
                            st.session_state["kp_hits"] = {
                                "coll": _hc, "items": _items,
                                "query": _h.get("query", ""), "err": ""}
                            st.session_state["kp_msg"] = (
                                "已删除：" + str(_it.get("id")) + "（原 " + _hc + "）")
                        except Exception as e:
                            root_logger.error("知识库删除失败：%s" % str(e)[:200])
                            st.session_state["kp_msg"] = "删除失败：" + str(e)[:120]
                        st.session_state["kp_del_idx"] = None
                        st.rerun()
                    if _d2.button("取消", key="kp_del_cancel_" + str(_i)):
                        st.session_state["kp_del_idx"] = None
                        st.rerun()

                st.divider()

            if _hc not in _PERMANENT_COLLS:
                st.caption(
                    "该集合不参与自动归档（scan_and_archive 只扫 memory_core / "
                    "memory_cold），所以这里不提供「设为永久」——写了也没人读。"
                )

    st.divider()

    # ---------- 待仲裁队列 ----------
    st.subheader("⚖️ 待仲裁知识（冲突 / 互补）")
    try:
        _pending = knowledge_service.list_pending()
    except Exception as e:
        root_logger.error("待仲裁队列读取失败：%s" % str(e)[:120])
        _pending = []
        st.warning("待仲裁队列读取失败：" + str(e)[:120])

    if not _pending:
        st.caption("没有待仲裁条目")
    else:
        st.caption("共 " + str(len(_pending)) + " 条待处理")
    for _pi, _p in enumerate(_pending):
        _st = str(_p.get("status", ""))
        st.write(str(_pi + 1) + ". " + _STATUS_CN.get(_st, _st) + "　" +
                 str(_p.get("detail", "")))
        st.text(str(_p.get("text") or "")[:400])
        _p1, _p2, _p3 = st.columns(3)
        if _p1.button("采用新知识", key="kp_keep_" + str(_pi)):
            try:
                r = knowledge_service.resolve_pending(_pi, keep_new=True)
                st.session_state["kp_msg"] = (
                    "已采用第 " + str(_pi + 1) + " 条，剩余 " +
                    str(r.get("remaining", 0)) + " 条")
            except Exception as e:
                root_logger.error("采用待仲裁失败：%s" % str(e)[:200])
                st.session_state["kp_msg"] = "采用失败：" + str(e)[:120]
            st.rerun()
        if _p2.button("丢弃新知识", key="kp_drop_" + str(_pi)):
            try:
                r = knowledge_service.resolve_pending(_pi, keep_new=False)
                st.session_state["kp_msg"] = (
                    "已丢弃第 " + str(_pi + 1) + " 条，剩余 " +
                    str(r.get("remaining", 0)) + " 条")
            except Exception as e:
                root_logger.error("丢弃待仲裁失败：%s" % str(e)[:200])
                st.session_state["kp_msg"] = "丢弃失败：" + str(e)[:120]
            st.rerun()
        st.divider()
