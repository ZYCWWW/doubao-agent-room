"""
科研七步流水线 · 功能插件（2026-11-09 插件化试点）

对外提供三样东西：
- plugin_export：research_create / research_step / research_status 三个工具
- panel_render：面板「🔬 科研七步」整页
- manifest 里的 config 段：是否启用

业务实现在 core/research（它依赖 llm_gateway / model_router / external_kb 等核心服务，
本来就属于「核心能力的组合」），本插件负责把它们当成一套可装可卸的功能暴露出来。
"""
import json

from core.research import (create_research, get_research, research_step,
                           list_research, STEPS)

_STEP_NAMES = {k: v[0] for k, v in STEPS.items()}


# ---------------- 工具 ----------------
def _research_create(research_topic: str, session_id: str = ""):
    r = create_research(research_topic)
    return (f"研究课题已创建：{r['topic_id']}\n当前第 {r['step']} 步（{r['step_name']}）：{r['prompt']}\n"
            f"请用 research_step(topic_id={r['topic_id']}, step={r['step']}, input_text=...) 提交本步输入")


def _research_step(topic_id: str, step: int, input_text: str, session_id: str = ""):
    r = research_step(topic_id, step, input_text)
    if not r["ok"]:
        return f"研究步骤失败：{r.get('error', '')}"
    out = [f"第{r['step']}步（{r['step_name']}）完成，结果："]
    out.append(json.dumps(r["result"], ensure_ascii=False, indent=2)[:1500])
    if r.get("next_step"):
        out.append(f"\n下一步：第 {r['next_step']} 步（{_STEP_NAMES[r['next_step']]}）——{STEPS[r['next_step']][1]}")
    else:
        out.append(f"\n研究已{r['status']}")
    return "\n".join(out)


def _research_status(topic_id: str = "", session_id: str = ""):
    if not topic_id:
        rows = list_research()
        if not rows:
            return "暂无研究课题"
        return "\n".join(f"- {t['topic_id']} | {t['topic']} | 第{t['step']}步 | {t['status']}" for t in rows)
    r = get_research(topic_id)
    if not r["ok"]:
        return f"查询失败：{r.get('error', '')}"
    lines = [f"研究课题 {r['topic_id']}：{r['topic']} | 状态 {r['status']}"]
    for k in sorted(r["steps"], key=int):
        lines.append(f"  step{k} {_STEP_NAMES[int(k)]}: {json.dumps(r['steps'][k], ensure_ascii=False)[:200]}")
    return "\n".join(lines)


plugin_export = {
    "research_create": {"desc": "创建科研七步流水线课题，返回 topic_id 与当前步骤指引", "func": _research_create},
    "research_step": {"desc": "提交科研流水线某一步的输入并执行（topic_id, step, input_text）", "func": _research_step},
    "research_status": {"desc": "查询科研课题进度（空 topic_id 列出全部）", "func": _research_status},
}


# ---------------- 面板页 ----------------
def panel_render():
    """渲染「🔬 科研七步」整页。streamlit 延迟导入，避免主进程加载插件时拉起 UI 依赖。"""
    import streamlit as st

    st.header("🔬 科研七步流水线")
    st.caption("确定研究对象 → 定性分析（相关性大→小）→ 因果假设 → 现实验证 → "
               "公共量收敛 → 定量推导 → 真理性验证")

    with st.expander("ℹ️ 怎么用", expanded=False):
        st.markdown("""
1. 填一个研究课题（如「水的沸点与海拔高度的关系」）点**创建**；
2. 每一步都会检索网页 / 本地知识库 / 国标题录 / 本地资料库，
   再让模型在这份材料上推导；
3. 第 2、4 步会自动检索（较慢，扫描版 PDF 会按需 OCR），其余步骤靠前序推导；
4. 也可以直接对模型说「用 research_step 继续第 N 步」。
""")

    # 新建课题
    with st.expander("➕ 新建课题", expanded=False):
        _topic = st.text_input("研究课题", key="rp_new_topic",
                               placeholder="例如：水的沸点与海拔高度的关系")
        if st.button("创建", key="rp_create_btn"):
            if not _topic.strip():
                st.warning("先填课题")
            else:
                try:
                    r = create_research(_topic.strip())
                    st.success(f"已创建 {r['topic_id']}，当前第 {r['step']} 步（{r['step_name']}）")
                except Exception as e:
                    st.error(f"创建失败：{e}")

    rows = list_research()
    if not rows:
        st.info("还没有课题。展开上面的「新建课题」，填一个研究课题点创建即可开始。")
        return

    st.divider()
    st.markdown(f"**现有课题（{len(rows)} 个）**")
    _label = {f"{t['topic'] }（第{t['step']}步·{t['status']}）": t["topic_id"] for t in rows}
    _sel = st.selectbox("选择课题", list(_label.keys()), key="rp_pick")
    _tid = _label[_sel]

    r = get_research(_tid)
    if not r.get("ok"):
        st.error(f"读取失败：{r.get('error', '')}")
        return

    st.markdown(f"#### {r['topic']}")
    _done = {int(k) for k in r["steps"]}
    _cur = r["step"]
    st.progress(len(_done) / 7, text=f"已完成 {len(_done)}/7 步")

    for i in sorted(STEPS):
        _name, _hint = STEPS[i]
        _is_done = i in _done
        _icon = "✅" if _is_done else ("👉" if i == _cur else "⬜")
        with st.expander(f"{_icon} 第{i}步 {_name}", expanded=(i == _cur and not _is_done)):
            st.caption(_hint)
            if _is_done:
                # steps 的键可能是 int（内存态）也可能是 str（读盘后），两种都认
                _key = i if i in r["steps"] else str(i)
                _data = r["steps"].get(_key)
                st.markdown(f"```json\n{json.dumps(_data, ensure_ascii=False, indent=2)[:1200]}\n```")

    st.divider()
    st.markdown("**推进下一步**")
    _step_default = r["step"] if r["step"] <= 7 else 7
    _step_pick = st.selectbox("步骤", sorted(STEPS), index=sorted(STEPS).index(_step_default),
                              format_func=lambda i: f"第{i}步 {_STEP_NAMES[i]}", key="rp_step_pick")
    _note = st.text_area("本步补充材料（可空）", height=90, key="rp_step_note",
                         placeholder="可以贴一段数据、观测或文献摘录；留空则只靠自动检索和前三步结论")
    if st.button("▶️ 执行这一步", key="rp_run_btn"):
        with st.spinner(f"第{_step_pick}步执行中（含外部检索，可能需要 1~3 分钟）…"):
            try:
                res = research_step(_tid, _step_pick, _note or "")
                if res.get("ok"):
                    st.success(f"第{res['step']}步完成")
                    st.markdown(f"```json\n{json.dumps(res['result'], ensure_ascii=False, indent=2)[:1500]}\n```")
                else:
                    st.error(f"失败：{res.get('error', '')}")
            except Exception as e:
                st.error(f"执行异常：{e}")
    st.caption(f"课题 ID：`{_tid}`｜研究目录：`sandbox/research/{_tid}/`")
