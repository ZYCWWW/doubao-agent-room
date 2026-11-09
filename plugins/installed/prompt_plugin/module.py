# -*- coding: utf-8 -*-
"""
提示词助手功能插件（2026-11-09）

从内置迁出：
  - 工具：prompt_optimize / prompt_submit（原 tools/prompt_optimize_tool.py）
  - 面板：✍️ 提示词助手（原 dashboard.py Tab8，下线后代码留在 `if False` 里）
业务本体仍在 services/prompt_engineer.py，本插件只做壳：工具注册 + 整页 UI。
"""
import json

from services.prompt_engineer import prompt_engineer


# ---------------- 工具 ----------------

def prompt_optimize(prompt: str, style: str = "通用", **kwargs):
    """优化提示词并返回对比结果（不执行）"""
    try:
        result = prompt_engineer.optimize(prompt, style=style)
    except Exception as e:
        return json.dumps({"success": False, "error": f"优化异常: {e}"}, ensure_ascii=False)
    if not result.get("success"):
        return json.dumps(result, ensure_ascii=False)
    return prompt_engineer.compare(result["original"], result["optimized"], result.get("changes"))


def prompt_submit(prompt: str, model: str = None, system: str = None, **kwargs):
    """把提示词交给 LLM 执行"""
    try:
        result = prompt_engineer.submit(prompt, model=model, system=system)
    except Exception as e:
        return json.dumps({"success": False, "error": f"提交异常: {e}"}, ensure_ascii=False)
    if not result.get("success"):
        return json.dumps(result, ensure_ascii=False)
    return result["response"]


plugin_export = {
    "prompt_optimize": {
        "func": prompt_optimize,
        "desc": "提示词优化助手：优化用户提示词并返回原版/优化版对比与改进点，不直接执行。"
                "参数：prompt(待优化提示词), style(可选风格偏好)。",
    },
    "prompt_submit": {
        "func": prompt_submit,
        "desc": "将提示词交给 LLM 执行（用户确认优化结果后使用）。"
                "参数：prompt(提示词内容), model(可选指定模型), system(可选系统提示)。",
    },
}


# ---------------- 面板 ----------------

def panel_render():
    """✍️ 提示词助手整页"""
    import streamlit as st

    st.header("✍️ 提示词工程助手")
    st.caption("流程：先看优化输出 → 确认是所需 → 再提交给 LLM。优化用的是 light 模型，通常 10~30 秒。")

    action = st.radio("操作", ["优化提示词", "查看模板", "对比", "提交给 LLM"],
                      key="pp_action", horizontal=True)

    # ---- 1. 优化 ----
    if action == "优化提示词":
        raw = st.text_area("你的原始提示词", height=120, key="pp_raw",
                           placeholder="例如：帮我分析一下这个实验结果为什么不符合预期")
        if st.button("生成优化版本", key="pp_opt_btn"):
            if not raw.strip():
                st.warning("请输入提示词")
            else:
                with st.spinner("light 模型优化中..."):
                    r = prompt_engineer.optimize(raw)
                if not r.get("success"):
                    st.error(f"优化失败：{r.get('error', '')}")
                else:
                    st.session_state["pp_opt"] = r
                    st.session_state["pp_opt_text"] = r.get("optimized", "")

        r = st.session_state.get("pp_opt")
        if not r:
            st.info("还没有优化结果。上面填一条提示词点「生成优化版本」，这里会出现原版 / 优化版 / 改进点。")
        else:
            st.subheader("优化后提示词")
            st.text_area("优化输出", r.get("optimized", ""), height=160, key="pp_opt_show")
            if r.get("changes"):
                st.markdown("**改进点：**")
                for c in r["changes"]:
                    st.markdown(f"- {c}")
            if r.get("note"):
                st.info(r["note"])
            _nm = st.text_input("保存为模板（名字）", key="pp_tpl_name")
            if st.button("保存模板", key="pp_tpl_save") and _nm.strip():
                prompt_engineer.save_template(_nm.strip(), r.get("optimized", ""))
                st.success(f"已保存模板：{_nm.strip()}")

    # ---- 2. 模板 ----
    elif action == "查看模板":
        _tpls = prompt_engineer.list_templates()
        if not _tpls:
            st.info("还没有模板：先在上面「优化提示词」里保存一条。")
        for _t in _tpls:
            st.markdown(f"**{_t['name']}** · {_t.get('style', '')}")
            with st.expander("预览"):
                _data = prompt_engineer.get_template(_t["name"]) or {}
                st.code(_data.get("content", ""), language="markdown")
                if st.button("删除", key=f"pp_del_{_t['name']}"):
                    prompt_engineer.delete_template(_t["name"])
                    st.rerun()

    # ---- 3. 对比 ----
    elif action == "对比":
        a = st.text_area("版本A（原始）", height=100, key="pp_cmp_a")
        b = st.text_area("版本B（优化）", height=100, key="pp_cmp_b")
        if st.button("对比分析", key="pp_cmp_btn"):
            if not a.strip() or not b.strip():
                st.warning("两版都要填")
            else:
                # compare() 返回的是 Markdown 字符串（旧面板误当 dict 取 verdict，这里已修正）
                st.markdown(prompt_engineer.compare(a, b))
        if "pp_cmp_a" not in st.session_state or not st.session_state.get("pp_cmp_a"):
            st.caption("想快速试：把「优化提示词」里得到的原版和优化版分别粘到 A / B。")

    # ---- 4. 提交 ----
    else:
        st.caption("确认优化输出是所需的之后，再提交给 LLM 执行")
        text = st.text_area("要提交的提示词", height=120, key="pp_submit_text",
                            value=st.session_state.get("pp_opt_text", ""))
        if st.button("提交给 LLM", key="pp_submit_btn"):
            if not text.strip():
                st.warning("请输入提示词")
            else:
                with st.spinner("LLM 执行中（默认 main 模型，较慢）..."):
                    r = prompt_engineer.submit(text)
                if not r.get("success"):
                    st.error(f"提交失败：{r.get('error', '')}")
                else:
                    st.subheader("LLM 回复")
                    st.markdown(r.get("response", ""))
        if not st.session_state.get("pp_opt_text"):
            st.info("还没有优化结果可预填。可以先在「优化提示词」里生成一版，或直接在上面的框里写。")
