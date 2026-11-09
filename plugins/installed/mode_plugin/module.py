# plugins/installed/mode_plugin/module.py
"""运行模式插件（📊 运行模式，order 100）

原来属于内置插件 `tools/mode_tool.py`（2 工具）与面板页 11「监控会话 → 运行模式」tab。
本插件只做外层封装：工具照原样导出，页 11 那个 tab 整块搬成自带页。
策略本体 `core/mode/strategy.py` **一行未动**。

一个重要事实（决定这个面板是不是假功能）：
`mode_strategy` 是**进程内单例**，`_manual_mode` 与 `_samples` 都不落盘、不跨进程共享。
面板之所以能改到主循环的模式，是因为 `dashboard.py` 直接 `from main import react_loop`——
面板与主循环**同进程**。本插件同样 `from core.mode import mode_strategy`，仍在同一进程内，
所以这里的按钮对主循环真实有效。若以后把主循环拆成独立服务进程，这组按钮会静默失效，
那时需要把 `_manual_mode` 改成落盘状态（并同步刷新），不能只改这一页。
"""
from core.mode import mode_strategy, MODES

_MODE_CN = {"steady": "执行型（少发散，直接做）", "inquiry": "求证型（多探索，先论证）"}


def _mode_status(session_id: str = ""):
    st_ = mode_strategy.status()
    return (f"当前模式：{st_['predict']} | 手动指定：{st_['manual_mode'] or '无'}\n"
            f"样本数：{st_['samples']}（steady {st_['steady']} / inquiry {st_['inquiry']}）\n"
            f"可用模式：{'/'.join(MODES)}（mode_set 可手动指定）")


def _mode_set(mode: str, task: str = "", session_id: str = ""):
    if mode not in MODES:
        return f"非法模式：{mode}（可选：{'/'.join(MODES)}）"
    mode_strategy.set_manual_mode(mode)
    if task:
        mode_strategy.record_choice(task, mode, score=1.0)
    return f"已切换为 {mode} 模式" + ("，并记录为强标签" if task else "")


plugin_export = {
    "mode_status": {"desc": "查看当前运行模式（steady/inquiry）与策略学习状态", "func": _mode_status},
    "mode_set": {"desc": "手动指定运行模式（steady/inquiry）；传 task 时记录为强标签", "func": _mode_set},
}


def panel_render():
    import streamlit as st

    st.header("📊 运行模式")
    st.caption(
        "双层判定：手动指定 > 关键词预判 > 样本投票。"
        "策略本体在 core/mode/strategy.py，这里只做页面。"
        "改完立即对主循环生效（面板与主循环同进程）。"
    )

    _msg = st.session_state.pop("mp_msg", None)
    if _msg:
        st.success(_msg)

    st_ = mode_strategy.status()
    st.markdown(
        f"**当前生效模式：{st_['predict']}** —— {_MODE_CN.get(st_['predict'], '')}　|　"
        f"手动指定：{st_['manual_mode'] or '无（按规则自动判定）'}"
    )
    st.caption(
        f"样本数 {st_['samples']}（steady {st_['steady']} / inquiry {st_['inquiry']}）；"
        "样本少于 3 条且没有关键词信号时默认 steady。"
    )

    st.divider()
    st.subheader("🎛 手动指定")
    st.caption("手动指定会覆盖关键词与样本投票，直到点「清除手动指定」。")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("设为 steady", key="mp_steady"):
            mode_strategy.set_manual_mode("steady")
            st.session_state["mp_msg"] = "已切换为 steady（执行型）"
            st.rerun()
    with c2:
        if st.button("设为 inquiry", key="mp_inquiry"):
            mode_strategy.set_manual_mode("inquiry")
            st.session_state["mp_msg"] = "已切换为 inquiry（求证型）"
            st.rerun()
    with c3:
        if st.button("清除手动指定", key="mp_clear"):
            mode_strategy.clear_manual_mode()
            st.session_state["mp_msg"] = "已清除手动指定，回到自动判定"
            st.rerun()

    st.divider()
    st.subheader("🔎 关键词预判规则")
    st.caption(
        "任务里出现「猜想 / 科研 / 探索 / 求证 / 研究 / 假设 / 因果 / 推理 / 设计 / 头脑风暴」"
        "等词 → inquiry；出现「执行 / 操作 / 点击 / 打开 / 发送 / 输入 / 保存 / 删除 / 整理 / 备份 / 文件」"
        "等词 → steady；都没命中才退回样本投票。手动指定优先级最高。"
    )
    st.caption(
        "当前手动指定是进程内状态，不落盘：重启面板后会回到「无」。"
        "这是 core/mode/strategy.py 的既有行为，本插件未改动它。"
    )
