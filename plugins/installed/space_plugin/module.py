# plugins/installed/space_plugin/module.py
"""想象沙盘插件：Mermaid 绘图 / 沙盘文件浏览 / 节点与快照查看。

业务本体留在 services/imaginary_service.py 与 core/imagination/，这里只做
工具注册 + 整页面板。原来的「沙盘文件」页（dashboard 里 `if False:` 那段）
就是这页的前身，这次把入口真正接回来。
"""
import os
import re

from services.imaginary_service import imaginary_service

SANDBOX_ROOT = imaginary_service.sandbox_root

# 沙盘根下还混着 research / crew / chat_space / hypothesis 这类功能目录，
# 会话目录是 UUID 形态，用它把两者分开。
_SID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


# ---------------------------------------------------------------- 三个工具

def draw_mermaid(session_id: str, chart_name: str, mermaid_code: str):
    """在想象沙盘绘制 Mermaid 图（保存成 md）"""
    md = f"# {chart_name}\n```mermaid\n{mermaid_code}\n```"
    fp = imaginary_service.write_md(session_id, f"{chart_name}.md", md)
    return f"Mermaid绘图已保存到沙盘：{fp}"


def sandbox_list(session_id: str):
    """列出当前会话沙盘内的所有文件"""
    files = imaginary_service.list_files(session_id)
    if not files:
        return "（当前会话沙盘为空）"
    return f"共 {len(files)} 个文件：\n" + "\n".join(files[:100])


def sandbox_read(file_path: str, session_id: str = ""):
    """读取沙盘内文件全文（路径必须落在沙盘根目录内）"""
    root = os.path.normcase(os.path.abspath(SANDBOX_ROOT))
    p = os.path.normcase(os.path.abspath(file_path))
    if not p.startswith(root + os.sep):
        return f"❌ 只允许读取沙盘目录内的文件：{SANDBOX_ROOT}"
    try:
        return imaginary_service.read_md(os.path.abspath(file_path))
    except Exception as e:
        return f"读取失败：{e}"


plugin_export = {
    "draw_mermaid": {
        "desc": "在想象沙盘绘制Mermaid图（像人类一样用图形简洁表达关系/公式证明过程/结构/流程，保存md）",
        "func": draw_mermaid,
    },
    "sandbox_list": {
        "desc": "列出当前会话沙盘内的所有文件（长结果、草图、笔记都存在这里）",
        "func": sandbox_list,
    },
    "sandbox_read": {
        "desc": "读取沙盘内文件全文（路径须在沙盘目录内，用于读回之前存下的长结果细节）",
        "func": sandbox_read,
    },
}


# ---------------------------------------------------------------- 面板

def _latest_session() -> str:
    """没有 session_state 时的兜底：取沙盘根下最近改动的会话目录"""
    try:
        cands = [(os.path.getmtime(os.path.join(SANDBOX_ROOT, d)), d)
                 for d in os.listdir(SANDBOX_ROOT)
                 if os.path.isdir(os.path.join(SANDBOX_ROOT, d)) and _SID_RE.match(d)]
        if cands:
            return max(cands)[1]
    except Exception:
        pass
    return ""


def _all_sessions() -> list:
    """沙盘根下的所有会话目录，按最近改动倒序"""
    try:
        cands = [(os.path.getmtime(os.path.join(SANDBOX_ROOT, d)), d)
                 for d in os.listdir(SANDBOX_ROOT)
                 if os.path.isdir(os.path.join(SANDBOX_ROOT, d)) and _SID_RE.match(d)]
        return [d for _, d in sorted(cands, reverse=True)]
    except Exception:
        return []


def _fmt_size(path: str) -> str:
    """友好的大小显示：小于 1KB 给字节数，避免统统显示成 0 KB"""
    try:
        n = os.path.getsize(path)
    except OSError:
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _has_content(sid: str) -> bool:
    """这个会话的沙盘里有没有真东西（文件或节点）"""
    try:
        if any(os.path.isfile(f) and not f.lower().endswith(".db")
               for f in imaginary_service.list_files(sid)):
            return True
        return bool(imaginary_service.list_nodes(sid))
    except Exception:
        return False


def panel_render():
    import streamlit as st

    st.header("📝 想象沙盘")
    st.caption("Agent 的草稿纸：Mermaid 图、推理笔记、长结果都落在这里。业务代码仍在 services/ 与 core/imagination/。")

    sid = st.session_state.get("sid") or _latest_session()
    if not sid:
        st.info("还没拿到会话 ID。先在「🧭 Agent调度」页开一个会话，再回来看。")
        return

    # 浏览器每刷新一次 Streamlit 就新开一个 session（sid 会变），沙盘看着就像空了。
    # 所以这里给一个会话选择器，能切回历史会话看旧草稿。
    all_sids = _all_sessions()
    if sid in all_sids:
        all_sids.remove(sid)
    all_sids.insert(0, sid)

    # 刚开的新会话沙盘必然是空的，一进来就是空页；这种时候默认落在最近有内容的会话上。
    default_idx = 0
    if not _has_content(sid):
        for i, s in enumerate(all_sids):
            if _has_content(s):
                default_idx = i
                break

    pick = st.selectbox("查看哪个会话的沙盘", all_sids, index=default_idx, key="sp_sid")
    sid = pick

    st.write("会话：" + sid)
    st.caption("沙盘根： " + SANDBOX_ROOT)

    # list_files 直接 os.listdir，会把 nodes/、snapshots/ 子目录和索引库一并返回，
    # 这里只保留真正的文件（且排除索引 db），避免页面上出现噪音条目。
    files = [f for f in imaginary_service.list_files(sid)
             if os.path.isfile(f) and not f.lower().endswith(".db")]
    try:
        nodes = imaginary_service.list_nodes(sid)
    except Exception:
        nodes = []
    try:
        snaps = imaginary_service.list_snapshots(sid)
    except Exception:
        snaps = []

    c1, c2, c3 = st.columns(3)
    c1.metric("文件", len(files))
    c2.metric("节点", len(nodes))
    c3.metric("快照", len(snaps))

    t1, t2, t3 = st.tabs(["📄 文件", "🧩 节点", "📸 快照"])

    with t1:
        if not files:
            st.info("这个会话的沙盘还是空的。让模型画一张 Mermaid 图或写一段草稿，这里就有东西了。")
        else:
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for fp in files[:80]:
                name = os.path.basename(fp)
                with st.expander(f"📄 {name}（{_fmt_size(fp)}）"):
                    st.caption(fp)
                    if name.lower().endswith((".md", ".txt")):
                        try:
                            content = imaginary_service.read_md(fp)
                        except Exception as e:
                            st.error(f"读取失败：{e}")
                            continue
                        if len(content) > 6000:
                            st.markdown(content[:6000])
                            st.caption(f"……内容较长，只显示前 6000 字符（全文 {len(content)}）")
                        else:
                            st.markdown(content)
                    else:
                        st.caption("非文本文件，不在此预览。")
            if len(files) > 80:
                st.caption(f"共 {len(files)} 个文件，只列出最近 80 个。")

    with t2:
        if not nodes:
            st.info("还没有节点。节点是沙盘的结构化骨架（标题 + 类型 + 父子关系）。")
        else:
            st.caption(f"共 {len(nodes)} 个节点")
            for nd in nodes[:60]:
                with st.expander(f"🧩 {nd.get('title', nd.get('node_id'))}　`{nd.get('type', '')}`"):
                    st.caption("node_id: " + str(nd.get("node_id")))
                    try:
                        body = imaginary_service.read_node(sid, nd["node_id"])
                    except Exception as e:
                        st.error(f"读取失败：{e}")
                        continue
                    st.markdown(body[:4000] + ("\n……" if len(body) > 4000 else ""))

    with t3:
        st.caption("快照 = 对当前节点树做全量拷贝，可回滚。")
        label = st.text_input("快照标签（可空）", key="sp_snap_label")
        if st.button("📸 创建快照", key="sp_snap_btn"):
            try:
                name = imaginary_service.snapshot(sid, label.strip())
                st.success(f"已创建快照：{name}")
                st.rerun()
            except Exception as e:
                st.error(f"创建失败：{e}")
        if not snaps:
            st.info("还没有快照。上面点「创建快照」就有了。")
        else:
            for sp in snaps:
                import time as _t
                ts = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(sp.get("created_ts", 0)))
                # 这一版 Streamlit 会把同一行里的多个 inline 格式片段拆成多行渲染，
                # 所以内容行只用纯文本，不加反引号也不加粗。
                st.markdown(f"- {sp.get('name')} —— {ts}")
