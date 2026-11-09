# plugins/installed/debate_plugin/module.py
"""辩论现场插件：四角色辩论的发起 / 逐轮发言 / 共识提取 / 会话列表。

业务本体一行没动：状态机在 core/debate/scheduler.py，收敛判定在
core/debate/consensus.py，共识提取在 core/debate/extractor.py，
services/debate_service.py 只是门面。这里把 tools/debate_tool.py 里的四个
薄封装搬过来，再补一页面板，让辩论在页面上能发起、能插话、能看结果。

四个工具的函数体逐字照搬，没有改行为。

注意：辩论会话存在 core/debate/scheduler.py 的模块级字典 _sessions 里，
是**进程内**的。所以面板进程新建的辩论只在面板里看得到，模型侧（main.py）
看不到，反之亦然。两边要互通得靠工具调用时在同一进程里跑。
"""
from core.debate import get_session, MAX_ROUNDS, CONVERGE_THRESHOLD
from services.debate_service import (debate_start, debate_speak,
                                     debate_extract, debate_list, debate_roles)

plugin_export = {
    "debate_start": {
        "desc": "发起多角色辩论（proposer/critic/constraint/synthesizer），返回 debate_id",
        "func": debate_start,
    },
    "debate_speak": {
        "desc": "在辩论中加入一轮发言（debate_id, role, content），返回收敛分数",
        "func": debate_speak,
    },
    "debate_extract": {
        "desc": "提取辩论的共识/分歧/开放问题清单",
        "func": debate_extract,
    },
    "debate_list": {
        "desc": "列出辩论会话及其状态",
        "func": debate_list,
    },
}


# ---------------------------------------------------------------- 整页面板
#
# 这一版 Streamlit 对「一行里既有 inline 格式又有中文标点」处理不稳，
# 一个 list item 会被拆成两行渲染（space_plugin 那次踩过）。本页内容行
# 一律纯文本或 st.text，不套反引号也不套加粗。

_STATUS_CN = {
    "running": "进行中",
    "converged": "已收敛",
    "max_rounds": "轮次到顶",
    "aborted": "已中止",
}


def _fmt_score(v):
    try:
        return "%.3f" % float(v)
    except (TypeError, ValueError):
        return str(v)


def panel_render():
    import streamlit as st

    st.header("🗣️ 辩论现场")
    st.caption("四角色（proposer / critic / constraint_checker / synthesizer）按轮发言，"
               "共识分 ≥ " + str(CONVERGE_THRESHOLD) + " 收敛，" + str(MAX_ROUNDS) + " 轮封顶。"
               "业务本体在 services/debate_service.py 与 core/debate/，这里只做入口。")

    # 一次性提示：发起 / 提交成功后会写进 dp_msg 并 rerun，这里显示完就清掉
    _msg = st.session_state.pop("dp_msg", None)
    if _msg:
        st.success(_msg)

    _has_any = bool(debate_list())

    with st.expander("✚ 发起新辩论", expanded=not _has_any):
        topic = st.text_input("辩题", key="dp_topic",
                              placeholder="例如：本地 4B 模型能否替代云端大模型做日常写作")
        if st.button("发起辩论", key="dp_start_btn"):
            tt = (topic or "").strip()
            if not tt:
                st.warning("先填辩题")
            else:
                r = debate_start(tt)
                st.session_state["dp_last"] = r
                st.session_state["dp_msg"] = "已发起：辩题编号 " + str(r.get("debate_id"))
                st.rerun()

    # 必须在 expander 之后重新取一次：上面的按钮可能刚新建了一场辩论，
    # 若在 expander 之前取，页面会停在「共 0 场辩论」（2026-11-09 踩过）
    sessions = debate_list()

    last = st.session_state.get("dp_last")
    if last:
        st.caption("刚发起：" + str(last.get("topic")) + "（编号 " + str(last.get("debate_id"))
                   + "，最多 " + str(last.get("max_rounds")) + " 轮）")

    st.divider()
    st.write("共 " + str(len(sessions)) + " 场辩论")

    if not sessions:
        st.info("还没有辩论。展开上面的「发起新辩论」填个辩题试试。")
        st.caption("提示：辩论会话是进程内的，面板新建的这场只在面板里可见；"
                   "模型侧用 debate_start / debate_speak 发起的那场在模型进程里，切不过来。")
        return

    ids = [s["id"] for s in sessions]

    def _label(did):
        # 下拉只显示主题和状态：发言条数 / 共识分随每轮变化，写进 label 会因
        # Streamlit 缓存旧选项文本而显示过期数字（2026-11-09 踩过），
        # 实时数值统一放到下方指标行
        for s in sessions:
            if s["id"] == did:
                return str(s["topic"]) + "　[" + _STATUS_CN.get(s["status"], s["status"]) + "]"
        return did

    did = st.selectbox("看哪一场", ids, key="dp_pick", format_func=_label)
    s = get_session(did)
    if s is None:
        st.warning("这场辩论不在当前进程里（可能只存在于模型进程），刷新页面试试。")
        return

    st.write("辩题：" + str(s.topic))
    st.write("编号：" + str(s.id) + "　状态：" + _STATUS_CN.get(s.status, s.status)
             + "　发言：" + str(len(s.turns)) + " 条　共识分：" + _fmt_score(s.consensus_score))
    st.caption("角色配额：" + "　".join(r["name"] + "×" + str(r.get("quota", 0))
                                      for r in s.roles))

    t_log, t_say, t_ext = st.tabs(["📜 发言记录", "✍️ 手动插话", "📋 共识提取"])

    with t_log:
        if not s.turns:
            st.info("还没有发言。切到「✍️ 手动插话」补一轮，或让模型侧用 debate_speak 推进。")
        else:
            for i, tr in enumerate(s.turns, 1):
                st.write(str(i) + ". " + str(tr.role))
                st.text(str(tr.content))
                if tr.tags:
                    st.caption("　标记：" + " / ".join(str(x) for x in tr.tags))
                st.divider()

    with t_say:
        if s.status != "running":
            st.warning("这场辩论已结束（" + _STATUS_CN.get(s.status, s.status)
                       + "），不能再插话。")
        else:
            role_names = [r["name"] for r in s.roles]
            role = st.selectbox("以谁的身份", role_names, key="dp_role")
            hint = ""
            for r in s.roles:
                if r["name"] == role:
                    hint = r.get("prompt_hint", "")
            st.caption("这个角色的任务：" + hint)
            # text_area 的 key 带序号：提交成功后把序号 +1，下次 rerun 就是全新
            # 空框。直接在按钮里写 st.session_state["dp_say"] = "" 会抛
            # StreamlitAPIException（widget 已实例化后不能改它的 key），
            # 异常被 registry 捕获后会退回兜底提示（2026-11-09 踩过）。
            seq = st.session_state.get("dp_say_seq", 0)
            content = st.text_area("发言内容", key="dp_say_" + str(seq), height=180,
                                   placeholder="写下这一轮的观点、理由、反例或约束检查结果")
            if st.button("提交这一轮", key="dp_say_btn"):
                cc = (content or "").strip()
                if not cc:
                    st.warning("发言内容不能为空")
                else:
                    r = debate_speak(s.id, role, cc)
                    if r.get("ok"):
                        st.session_state["dp_msg"] = ("已记入第 " + str(r.get("round")) + " 轮，共识分 "
                                                       + _fmt_score(r.get("consensus_score")) + "，状态 "
                                                       + _STATUS_CN.get(r.get("status"), str(r.get("status"))))
                        st.session_state["dp_say_seq"] = seq + 1
                        st.rerun()
                    else:
                        st.error(str(r.get("error", "发言失败")))

    with t_ext:
        st.caption("未收敛也可以提取，看到的就是当下的临时结论。")
        if st.button("📋 提取共识 / 分歧 / 开放问题", key="dp_ext_btn"):
            r = debate_extract(s.id)
            st.session_state["dp_ext"] = r

        ext = st.session_state.get("dp_ext")
        if ext:
            if not ext.get("ok"):
                st.error("提取失败：" + str(ext.get("error")))
            else:
                st.write("状态：" + _STATUS_CN.get(ext.get("status"), str(ext.get("status")))
                         + "　编号：" + str(ext.get("debate_id")))
                st.write("共识")
                st.text(str(ext.get("consensus") or "（尚未形成）"))
                st.write("分歧（" + str(len(ext.get("disagreements") or [])) + " 条）")
                dis = ext.get("disagreements") or []
                if dis:
                    for d in dis:
                        st.text("· " + str(d))
                else:
                    st.caption("　没有记录到明确分歧")
                st.write("开放问题（" + str(len(ext.get("open_questions") or [])) + " 条）")
                oq = ext.get("open_questions") or []
                if oq:
                    for q in oq:
                        st.text("· " + str(q))
                else:
                    st.caption("　没有留下开放问题")
        else:
            st.info("点上面的按钮提取一次。")
