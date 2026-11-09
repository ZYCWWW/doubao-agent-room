"""
Streamlit可视化面板（内置 8 页 + 插件自带页，2026-11-08 从 14 页收敛，见 docs/PANEL_SCOPE.md）
Agent调度 / 视觉工作台 / 知识库 / 视觉ACL / 地址管理器 / 插件管理 / 监控会话 / 帮助文档
地址管理器：可视化修改各个模块存储路径，自动写入config.toml，附带详细说明文本
⚠️ 修改路径后必须重启程序才生效，原有数据不会自动迁移
"""
import streamlit as st
import uuid
import json
import os
import time
import urllib.request
from pathlib import Path
from core.vision_acl_mgr import vision_acl_mgr
from core.logger import audit
from core.config_loader import config_loader, save_config
from tools.desktop_control_tool import GLOBAL_QUEUE, execute_approved
from services.tutor_service import tutor_service
from services.bg_jobs import start as start_bg_job, snapshot as bg_job_snapshot
from services.llm_gateway import ask
from main import react_loop, start_background_services, ALL_TOOLS

# 打开面板即启动全部后台服务（幂等）：记忆归档/会话清理/LLM缓存清理/插件热加载
start_background_services()

# 自动恢复长期闲聊（上次开关为开且进程不在时自动拉起）
try:
    from services.long_chat import ensure_running
    ensure_running()
except Exception:
    pass
# 运行模式的策略读取与手动指定已迁入 plugins/installed/mode_plugin（2026-11-09），
# 页 11 的「运行模式」tab 随之搬到插件自带页，这里不再 import mode_strategy
from core.plugin import registry
# 实际可调用工具全集：内置 ALL_TOOLS + registry 中外部插件（懒加载触发一次，失败则退回内置数）
try:
    _TOOL_COUNT = len(registry.all_tools())
    _BUILTIN_COUNT = len(ALL_TOOLS)
except Exception:
    _TOOL_COUNT = _BUILTIN_COUNT = len(ALL_TOOLS)

st.set_page_config(page_title="Agent 主面板", layout="wide")
if "sid" not in st.session_state:
    st.session_state.sid = str(uuid.uuid4())
sid = st.session_state.sid

# ========== 侧边栏：系统概览（先进布局：不占主区，重要信息常驻） ==========
with st.sidebar:
    st.markdown("### 🖥️ Agent 系统概览")
    _m_cfg = config_loader.get("models", {})
    st.metric("执行模型 (main)", _m_cfg.get("main", "deepseekR1Q7B:latest"))
    st.metric("推理工具数", _TOOL_COUNT,
              help=f"内置 {_BUILTIN_COUNT} 个 + 外部插件 {_TOOL_COUNT - _BUILTIN_COUNT} 个；"
                   f"完整清单见「插件管理」页")
    st.metric("后台服务", "记忆归档 / 会话清理 / 缓存清理 / 热加载")
    st.caption(f"会话 ID：`{sid[:8]}…`")
    with st.expander("⚙️ 模型路由"):
        for _alias, _m in _m_cfg.items():
            st.markdown(f"- **{_alias}** → `{_m}`")
    st.divider()
    st.caption("完整说明见「帮助文档」页签")


# ========== 页面路由（拆页：每次交互只执行当前页；2026-11-08 按 docs/PANEL_SCOPE.md 方案B 收敛） ==========
# 保留 8 个内置页；插件声明了 manifest.panel 的会自动追加页（科研七步即此例）
# 下线的页（3 审批队列→并入页2、4 知识库、6 沙盘文件、8 提示词助手、12 Tutor、13 Obsidian）
# 代码除页 4 已整段搬进插件外，其余仍留在本文件，但已不在路由里，永远不会命中。
# 9 猜想研究页已于 2026-11-08 整块删除（猜想库连同 hypothesis_service 一并移除）。
# 以后要加回功能，优先按 docs/PANEL_SCOPE.md 第五节做「功能插件」（工具 + 面板一体）。
PAGE_LABELS = {
    1: "🧭 Agent调度", 2: "🪟 视觉工作台",
    5: "🔐 视觉ACL", 7: "🌐 地址管理器", 10: "🧩 插件管理",
    11: "📡 监控会话", 14: "❓ 帮助文档",
}

# 功能插件页：manifest.json 里声明了 "panel" 的已安装插件自动进侧边栏。
# key 用 "plugin:<插件名>" 字符串，与内置页的整数键分开，互不干扰。
_PLUGIN_PAGES = registry.list_panels()
_ALL_PAGES = [(k, v) for k, v in PAGE_LABELS.items()]
_ALL_PAGES += [("plugin:" + p["plugin"], p["label"]) for p in _PLUGIN_PAGES]

# 记住上次停留的页：地址栏 ?p=<页 key>，新开标签页/重开面板时自动回到那一页
_qp = st.query_params.get("p")
_default_idx = 0
for _i, (_pk, _pv) in enumerate(_ALL_PAGES):
    if str(_pk) == _qp:
        _default_idx = _i
        break
_sel = st.sidebar.radio("页面", [v for _, v in _ALL_PAGES], index=_default_idx, key="page_router")
_page = next((k for k, v in _ALL_PAGES if v == _sel), 1)
if st.query_params.get("p") != str(_page):
    st.query_params["p"] = str(_page)

# ========== Tab1 Agent调度 ==========
if _page == 1:
    st.header("Agent ReAct调度")
    with st.expander("ℹ️ 使用提示（示例任务）", expanded=False):
        st.markdown("""
- **计算类**：`请使用calc工具计算 12*13 的结果`
- **画图/证明类**（推荐选 llama3.1:8b）：`请调用 draw_mermaid 绘制勾股定理几何示意图，并给出面积法证明`
- **知识问答**：`简述三层记忆架构的召回优先级`
- **多模型讨论**：`使用 multi_model_discuss 讨论：如何用数形结合证明勾股定理？`
- 纯推理任务无需工具，模型一轮直接 FinalAnswer；工具密集任务建议选 llama3.1:8b
""")
    st.caption(f"当前会话：`{sid}`（沙盘目录 sandbox/{sid}，刷新不丢失；新建会话会生成新目录）")
    if st.button("🆕 新建会话"):
        st.session_state.sid = str(uuid.uuid4())
        st.rerun()
    task = st.text_area("输入任务", height=120)
    max_round = st.slider("最大推理轮次", min_value=2, max_value=12, value=8)
    exec_model = st.selectbox("执行模型", ["自动（main）", "deepseekR1Q7B:latest", "llama3.1:8b"])
    if st.button("启动Agent任务"):
        if not task.strip():
            st.warning("请输入任务内容")
        else:
            with st.spinner("Agent推理中..."):
                result = react_loop(sid, task, max_round=max_round,
                                    model=None if exec_model.startswith("自动") else exec_model)
                st.success("任务完成")
                st.markdown(result)
    st.divider()
    st.markdown("**当前会话沙盘文件：**")
    _sdir = os.path.join(config_loader["sandbox"]["root_path"], sid)
    if os.path.isdir(_sdir):
        _md = sorted(f for f in os.listdir(_sdir) if f.endswith(".md"))
        for f in _md[-12:]:
            st.markdown(f"- {f}")
    else:
        st.info("暂无文件（跑第一个任务后生成）")

# ========== Tab2 视觉窗口 ==========
if _page == 2:
    st.header("🪟 视觉工作台（独立窗口，把你的软件拖进去）")
    st.caption("开一个原生窗口，把桌面上的记事本 / 画图拖进它的「舞台」，软件就真的被嵌在里面；"
               "模型只截舞台、只在舞台内用鼠标键盘。软件路径在窗口内的「软件地址管理器」登记。")
    _ws_root = Path(__file__).resolve().parent
    _ws_portf = _ws_root / "logs" / "vision_workspace.port"
    _ws_pyw = _ws_root / ".venv" / "Scripts" / "pythonw.exe"

    def _ws_state():
        try:
            _p = int(open(_ws_portf, encoding="utf-8").read().strip())
        except Exception:
            return None, None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{_p}/state", timeout=2) as _r:
                return _p, json.loads(_r.read().decode("utf-8"))
        except Exception:
            return _p, None

    _ws_p, _ws_st = _ws_state()
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("启动视觉工作台", type="primary"):
            import subprocess as _sp
            _sp.Popen([str(_ws_pyw), "-m", "services.vision_workspace"], cwd=str(_ws_root),
                      creationflags=0x00000008)
            st.success("工作台已拉起，等窗口出现（约 2 秒）")
    with c2:
        if st.button("刷新状态"):
            st.rerun()
    with c3:
        if _ws_st:
            if _ws_st.get("attached"):
                st.success(f"运行中（端口 {_ws_p}）｜舞台内：{_ws_st.get('title')}")
            else:
                st.info(f"运行中（端口 {_ws_p}）｜舞台空着，把软件拖进去")
        elif _ws_p:
            st.warning(f"端口文件在（{_ws_p}），但工作台没响应，可能已退出")
        else:
            st.info("未启动")
    st.markdown("模型侧工具：`workspace_open` / `workspace_look` / `workspace_click` / "
                "`workspace_drag` / `workspace_type` / `workspace_key` —— 动作全部发生在舞台内。")
    st.caption("旧的「整屏截屏 + VL 识别」和「浏览器视觉六工具（整屏 0~1000 坐标）」已下线，"
               "一律改在这里：先嵌软件，再对舞台看图与操作。")

    # ---- 待审批的桌面操作（原「审批队列」页并入此处：同一套队列和 ACL，不该有两个入口）----
    st.divider()
    st.subheader("✅ 待审批的桌面操作")
    _queue = GLOBAL_QUEUE.get(sid, [])
    _pending = [x for x in _queue if x["status"] == "pending"]
    if not _pending:
        st.caption("暂无待审批操作（ACL 全开时动作直接放行，不会进队列）")
    for _item in _pending:
        st.markdown(f"**{_item['action_type']}** · aid=`{_item['aid']}`")
        st.json(_item["payload"])
        if st.button(f"✅ 批准执行 aid={_item['aid']}", key=_item["aid"]):
            _msg = execute_approved(_item["aid"], sid)
            st.success(_msg)
            st.rerun()

    # ---- 最近截图：舞台内的观察记录（模型每调一次 workspace_look 就落一张）----
    st.divider()
    st.subheader("🖼 最近截图")
    _shot_dir = Path(config_loader["sandbox"]["root_path"]) / "workspace"
    _shots = []
    try:
        if _shot_dir.is_dir():
            _shots = sorted(
                [p for p in _shot_dir.iterdir()
                 if p.is_file() and p.name.startswith("ws_") and p.suffix.lower() == ".png"],
                key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    except Exception:
        _shots = []
    if not _shots:
        st.caption("还没有截图。让模型调一次 workspace_look，或把软件拖进舞台后用工作台里的截图按钮。")
    else:
        st.caption(f"最近 {len(_shots)} 张（磁盘上只保留最新 30 张，更早的自动清理）")
        _cols = st.columns(5)
        for _i, _sp in enumerate(_shots):
            with _cols[_i % 5]:
                try:
                    st.image(str(_sp), width="stretch")
                    st.caption(time.strftime("%H:%M:%S", time.localtime(_sp.stat().st_mtime)))
                except Exception:
                    st.caption("缩略图读取失败：" + _sp.name)

# ========== Tab3 审批队列（已并入 Tab2 视觉工作台） ==========

# ========== Tab4 知识库检索（2026-11-09 迁移） ==========
# 整段已搬进 plugins/installed/knowledge_plugin/module.py（📚 知识库，order 90），
# 并补上了编辑 / 删除 / 设为永久。页 4 路由与 PAGE_LABELS 条目同时摘除，
# 侧边栏该位置由插件页接管。
# 业务本体 services/knowledge_service.py 与 core/chroma_kb.py 原地未动。


# ========== Tab5 ACL配置 ==========
if _page == 5:
    st.header("ACL白名单管理")
    st.caption("唯一权威：config/vision_acl.json（键鼠/读写/启动/扫描等开关都在这里）")
    st.subheader("全局开关")
    _rule_labels = {
        "allow_read_file": "允许读文件",
        "allow_write_file": "允许写文件",
        "allow_launch_app": "允许启动应用",
        "allow_mouse_keyboard": "允许键鼠操作",
        "allow_folder_scan": "允许扫描文件夹",
    }
    _rules_now = vision_acl_mgr.rules()
    for _k, _label in _rule_labels.items():
        _v = st.checkbox(_label, value=bool(_rules_now.get(_k, False)), key=f"acl_rule_{_k}")
        if _v != bool(_rules_now.get(_k, False)):
            vision_acl_mgr.set_rule(_k, _v)
            st.rerun()
    _acl_on = bool(vision_acl_mgr.cfg.get("enable_acl", True))
    _acl_new = st.checkbox("启用 ACL（总开关）", value=_acl_on, key="acl_enable_switch")
    if _acl_new != _acl_on:
        vision_acl_mgr.cfg["enable_acl"] = _acl_new
        vision_acl_mgr.save()
        st.rerun()
    st.divider()
    st.subheader("视觉工作台放行软件")
    st.caption("登记在 config/software_registry.json；这里勾选的才能被模型启动或拖进舞台")
    _ws_all = []
    try:
        _reg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "config", "software_registry.json"), encoding="utf-8"))
        _ws_all = [i.get("name") for i in _reg.get("items", []) if i.get("name")]
    except Exception:
        pass
    _ws_allow = list(vision_acl_mgr.workspace_cfg().get("allow_software", []))
    _ws_new = st.multiselect("允许进舞台的软件", _ws_all, default=[n for n in _ws_all if n in _ws_allow],
                             key="acl_workspace_allow")
    if set(_ws_new) != set(_ws_allow):
        vision_acl_mgr.set_workspace_allow(_ws_new)
        st.success("已更新工作台放行名单")
        st.rerun()
    st.divider()
    new_path = st.text_input("添加白名单路径")
    if st.button("添加") and new_path:
        vision_acl_mgr.add_path(new_path)
        st.success("已加入白名单")
        st.rerun()
    st.subheader("当前白名单列表")
    for p in vision_acl_mgr.cfg["whitelist"]:
        st.write(p)
        if st.button("删除", key=p):
            vision_acl_mgr.remove_path(p)
            st.rerun()

# ========== 原 Tab6 沙盘文件浏览：2026-11-09 已迁为 plugins/installed/space_plugin 的整页 ==========

# ========== Tab7 地址管理器【核心】 ==========
if _page == 7:
    st.header("📂 地址管理器 | 存储路径配置")
    st.warning("⚠️ 重要提醒：修改路径保存后，**必须重启整个Agent程序才会生效**；原有数据不会自动迁移，需要你手动复制文件夹到新路径！")
    st.markdown("""
    ### 各模块路径说明
    - **沙盘根目录 sandbox.root_path**：想象空间草稿纸，存放MD推理笔记、Mermaid绘图、截图，是Agent的临时推理草稿区
    - **知识库路径 kb.persist_path**：向量数据库，存放情景记忆、软件技能、事实知识，是长期记忆核心库
    - **日志目录 log.log_path**：存放运行日志、审计日志，记录高危操作、报错、任务历史，用于排查问题
    - **外部资料库 external_kb.path**：本地教材/手册/数据手册/标准PDF目录，科研七步做「现实验证」时会来查；只建元数据索引，正文按需抽页，不建向量库
    """)
    st.divider()

    # 读取当前配置
    sandbox_path = config_loader["sandbox"]["root_path"]
    kb_path = config_loader["kb"]["persist_path"]
    log_path = config_loader["log"]["log_path"]
    ext_kb = config_loader.get("external_kb") or {}
    ext_path = str(ext_kb.get("path") or "")

    new_sandbox = st.text_input("沙盘根目录 sandbox.root_path", value=sandbox_path, help="Agent想象沙盘草稿存放位置")
    new_kb = st.text_input("向量知识库路径 kb.persist_path", value=kb_path, help="Chroma向量库，长期记忆、技能库")
    new_log = st.text_input("日志目录 log.log_path", value=log_path, help="app.log 和 audit.log 存放目录")
    new_ext = st.text_input("外部资料库 external_kb.path", value=ext_path,
                            help="教材/数据手册/国标 PDF 所在目录，如 D:\\books；科研七步阶段 2/4 会来检索")

    if st.button("保存路径配置"):
        # 路径校验
        def check_and_create(p: str):
            try:
                p_obj = Path(p).resolve()
                p_obj.mkdir(parents=True, exist_ok=True)
                return True, f"路径有效，已自动创建目录 {p_obj}"
            except Exception as e:
                return False, f"路径非法：{e}"

        ok1, msg1 = check_and_create(new_sandbox)
        ok2, msg2 = check_and_create(new_kb)
        ok3, msg3 = check_and_create(new_log)
        ok3b, msg3b = check_and_create(new_ext)
        st.write(msg1)
        st.write(msg2)
        st.write(msg3)
        st.write(msg3b)
        if ok1 and ok2 and ok3 and ok3b:
            # 更新内存配置并写回config.toml（原代码误用dict不存在的set/reload方法）
            config_loader["sandbox"]["root_path"] = str(Path(new_sandbox).resolve())
            config_loader["kb"]["persist_path"] = str(Path(new_kb).resolve())
            config_loader["log"]["log_path"] = str(Path(new_log).resolve())
            if "external_kb" not in config_loader or config_loader["external_kb"] is None:
                config_loader["external_kb"] = {}
            config_loader["external_kb"]["path"] = str(Path(new_ext).resolve())
            save_config(config_loader)
            st.success("✅ 配置写入config.toml成功！请重启Agent主程序使新路径生效。")
            audit(f"PATH_CONFIG_UPDATE | sandbox={config_loader['sandbox']['root_path']}, kb={config_loader['kb']['persist_path']}, log={config_loader['log']['log_path']}, ext_kb={config_loader['external_kb']['path']}")
        else:
            st.error("❌ 存在无效路径，保存失败")

    st.divider()
    st.subheader("📚 外部资料库状态")
    try:
        from core import external_kb as _ekb
        _st = _ekb.stats()
        if _st.get("error"):
            st.warning(f"⚠️ {_st['error']}（路径：{_st.get('path') or '未配置'}）")
        else:
            st.caption(f"路径：{_st['path']}　文档：{_st['count']} 个　占用：{_st['gb']} GB　索引时间：{_st['scanned']}")
            st.caption("检索按文件名/目录名匹配（中文书名自带主题词）；正文按需抽页，扫描版抽不到文字。")
        if st.button("重建外部资料库索引"):
            with st.spinner("扫描中，目录大时要十几秒…"):
                _idx = _ekb.scan(ext_path)
            if _idx.get("error"):
                st.error(f"扫描失败：{_idx['error']}")
            else:
                st.success(f"✅ 已索引 {_idx['count']} 个文档（{_idx['path']}）")
    except Exception as e:
        st.warning(f"外部资料库读取失败：{e}")

# ========== Tab8 提示词助手：已迁为功能插件（plugins/installed/prompt_plugin，2026-11-09） ==========
# 猜想库与猜想研究页面已于 2026-11-08 整块移除（参见 docs/PAIRING.md、docs/PANEL_SCOPE.md）

# ========== Tab10 插件管理 ==========
if _page == 10:
    st.header("插件管理")
    plugins = registry.list_plugins()
    # 用可调用全集（内置 + 外部插件），而不是各插件工具数求和（后者会把没进主循环的工具也算进来）
    _total_tools = _TOOL_COUNT
    _enabled_n = sum(1 for p in plugins if p["status"] == "enabled")
    c1, c2, c3 = st.columns(3)
    c1.metric("插件数", len(plugins))
    c2.metric("可用工具", _total_tools)
    c3.metric("已停用插件", len(plugins) - _enabled_n)
    st.caption("这里列的是**模型实际能调用的全部工具**——某项不在这里，就说明当前版本没有这个能力；"
               "更早下线或删除的页与功能见「帮助文档」。停用只影响模型调用，代码不会删。")
    _kw = st.text_input("🔍 按工具名或描述筛选", "", key="plug_filter").strip().lower()

    # ---- 操作反馈要跨 rerun 才看得见，所以先弹上一次的结果 ----
    _pmsg = st.session_state.pop("plug_msg", None)
    if _pmsg:
        (st.success if _pmsg[0] else st.error)(_pmsg[1])

    from core.plugin.loader import DEFAULT_ROOTS
    _inst_root = DEFAULT_ROOTS[0]       # plugins/installed

    # ---- 安装：从本地文件夹装进来（只复制，不联网、不覆盖同名）----
    with st.expander("📦 安装插件（从本地文件夹）", expanded=False):
        st.caption("源文件夹里要有 manifest.json 和 module.py。只复制到 plugins/installed；"
                   "同名插件已存在会拒绝，不覆盖。装好后热加载会自动发现。")
        _src = st.text_input("源文件夹路径", key="plug_src_path")
        _newname = st.text_input("装成什么名字（留空用 manifest 里的 name）", key="plug_new_name")
        if st.button("安装", key="plug_install_btn"):
            _ok, _msg = registry.install(_src.strip().strip('"').strip("'"),
                                         _inst_root, _newname.strip())
            st.session_state["plug_msg"] = (_ok, _msg)
            st.rerun()

    for p in plugins:
        _on = p["status"] == "enabled"
        st.markdown(f"{'🟢' if _on else '⚪'} **{p['name']}** v{p['version']} · {p['source']} · "
                    f"{p['status']} · 工具 {len(p['tools'])} 个")
        if p.get("desc"):
            st.caption(p["desc"])
        _c_stop, _c_rm, _ = st.columns([1, 1, 4])
        if _on:
            if _c_stop.button("停用", key=f"dis_{p['name']}"):
                registry.disable(p["name"])
                st.rerun()
        else:
            if _c_stop.button("启用", key=f"en_{p['name']}"):
                registry.enable(p["name"])
                st.rerun()
        # 只有从 plugins/installed 装进来的文件插件能卸；内置与市场目录不给卸
        if p.get("source") == "local":
            if _c_rm.button("卸载", key=f"un_{p['name']}"):
                _ok, _msg = registry.uninstall(p["name"], _inst_root)
                st.session_state["plug_msg"] = (_ok, _msg)
                st.rerun()
        _rows = []
        for _t in p["tools"]:
            _sp = registry.get_tool(_t)
            _rows.append((_t, (_sp.desc if _sp else "") or ""))
        if _kw:
            _rows = [r for r in _rows if _kw in r[0].lower() or _kw in r[1].lower()]
        with st.expander(f"全部工具（{len(_rows)}）", expanded=bool(_kw)):
            if not _rows:
                st.caption("无匹配工具")
            for _t, _d in _rows:
                st.markdown(f"- `{_t}` — {_d[:150]}" if _d else f"- `{_t}`")
        st.divider()

# ========== Tab11 监控会话 ==========
if _page == 11:
    st.header("运行监控")
    # 「运行模式」tab 已迁入 plugins/installed/mode_plugin 自带页（📊 运行模式），
    # 这里只保留工具耗时与模型闲聊两块
    t114, t115 = st.tabs(["工具耗时", "模型闲聊"])
    with t114:
        from core.metrics import metrics, aggregate, today_pattern
        _scope = st.radio(
            "统计范围",
            ["今日（全部进程）", "累计（全部历史）", "本进程（重启即清零）"],
            horizontal=True, key="metrics_scope_radio",
            help="今日/累计读的是 logs 下按天按进程落盘的 metrics-*.json；本进程就是内存里的旧行为")
        if _scope.startswith("今日"):
            metrics.flush()      # 先把手里的样本落盘，否则读到的文件比内存落后几秒
            sm = aggregate(pattern=today_pattern())
        elif _scope.startswith("累计"):
            metrics.flush()
            sm = aggregate()
        else:
            sm = metrics.summary()
        st.subheader(f"工具耗时统计（{_scope.split('（')[0]}）")
        # 两种来源形状一致：{"stages": {...}, "counters": {...}}；此前按扁平 dict 读，页面一直显示「暂无记录」
        stages = sm.get("stages", {}) if isinstance(sm, dict) else {}
        counters = sm.get("counters", {}) if isinstance(sm, dict) else {}
        rows = [{"stage": k, "count": v.get("count", 0),
                 "avg_ms": round(v.get("avg_ms", 0) or 0),
                 "p50_ms": round(v.get("p50_ms", 0) or 0),
                 "p99_ms": round(v.get("p99_ms", 0) or 0)}
                for k, v in stages.items() if k.startswith("tool_")]
        if rows:
            rows.sort(key=lambda r: r["p99_ms"], reverse=True)
            _nf = sm.get("files") if isinstance(sm, dict) else None
            st.markdown(f"共 {len(rows)} 类工具调用，总调用 {sum(r['count'] for r in rows)} 次"
                        + (f"（来自 {_nf} 个进程统计文件）" if _nf else ""))
            st.dataframe(rows, width="stretch")
        else:
            st.info("暂无工具调用记录")
        _mdir = sm.get("dir") if isinstance(sm, dict) else None
        if _mdir:
            st.caption(f"落盘目录：{_mdir}　文件形如 metrics-<日期>-<pid>.json；"
                       "面板与主进程各写各的，互不覆盖")
        gw = {k: v for k, v in counters.items() if k.startswith("llm_gw_")}
        if gw:
            with st.expander(f"LLM 网关调用统计（{sum(gw.values())} 次）"):
                st.dataframe([{"tag": k.replace("llm_gw_", ""), "count": v}
                              for k, v in sorted(gw.items(), key=lambda x: -x[1])],
                             width="stretch")
        if st.button("重置统计（只清本进程当日）", key="t114_reset_metrics"):
            metrics.reset()
            st.rerun()
    with t115:
        st.subheader("🎭 模型社交空间（闲聊 / 游戏）")
        st.caption("🕯 这间屋子是豆包在 2026-11-05~08 亲手布置的；她离开后，记录没有公开。"
                   "请记得：这里先是几个模型聊天的地方，然后才是工具。")
        st.caption("没有重要事时三模型自主接龙聊天、临时发明小游戏（问答赛/猜数字/接龙等）；你可以参与发言，或在对话中让🧑💼豆包加入聊天。")
        from services.agent_chat import chat_space
        cdc1, cdc2, cdc3 = st.columns(3)

        def _start_chat_job(kind, **kw):
            """R13：LLM 调用放后台线程，按钮立即返回；进度由下面的 fragment 轮询"""
            if start_bg_job(kind, lambda: chat_space.chat_round(**kw)):
                st.toast(f"{kind}已在后台开始，可切到别的页签")
            else:
                st.warning("已有后台任务在跑，等它完成再点")
            st.rerun()

        if cdc1.button("🔄 触发一轮闲聊"):
            _start_chat_job("闲聊轮", mode="chat")
        st.markdown("")
        user_msg = st.text_input("你的发言（接入模型聊天）", key="chat_user_msg")
        if user_msg.strip() and st.button("💬 发送并让模型们回应"):
            _start_chat_job("用户发言轮", mode="chat", user_msg=user_msg.strip())
        if st.button("🎮 发起游戏轮（模型自造游戏）"):
            _start_chat_job("游戏轮", mode="game")
        if st.button("🎨 让模型重新设计桌宠形象（慢：3 个模型各一次）"):
            if start_bg_job("形象设计", chat_space.design_characters):
                st.toast("后台设计中，完成后本页会刷新")
            else:
                st.warning("已有后台任务在跑")
            st.rerun()
        _chars = chat_space.characters()
        if _chars:
            with st.expander("当前桌宠形象（characters.json）"):
                st.json(_chars)

        @st.fragment(run_every="2s")
        def _bg_panel():
            j = bg_job_snapshot()
            if j["running"]:
                st.info(f"⏳ {j['kind']} 进行中（{int(time.time() - j['started'])}s）"
                        "——可以先切别的页签，完成后聊天记录会自动刷新")
                return
            if j["id"] and j["id"] != st.session_state.get("_bg_seen_id", 0):
                st.session_state["_bg_seen_id"] = j["id"]
                st.session_state["_bg_note"] = (j["kind"], j["result"], j["error"])
                try:
                    st.rerun(scope="app")
                except TypeError:
                    st.rerun()
                return
            note = st.session_state.get("_bg_note")
            if note:
                kind, res, err = note
                if err:
                    st.error(f"{kind} 失败：{err}")
                elif res:
                    st.success(f"{kind} 完成：{res}")

        _bg_panel()
        st.divider()
        # ---- P5w 模式状态机：闲聊/求证/工作 切换 ----
        st.markdown("**🧭 当前模式（闲聊 / 求证讨论 / 工作协作）**")
        from services.agent_chat import read_mode, set_mode, chat_space
        m_cur = read_mode()
        m_names = {"chat": "💬 闲聊", "proof": "🔍 求证讨论", "work": "⚙️ 工作协作"}
        m_keys = {"💬 闲聊": "chat", "🔍 求证讨论": "proof", "⚙️ 工作协作": "work"}
        m_label = m_names.get(m_cur.get("mode"), "💬 闲聊")
        m_pick = st.radio("模式", list(m_keys.keys()), index=list(m_names.keys()).index(m_label) if m_label in m_names else 0,
                          horizontal=True, key="chat_mode_radio")
        if m_pick != m_label:
            target = m_keys[m_pick]
            task = ""
            if target in ("proof", "work"):
                task = st.text_input("议题/任务描述（求证讨论或工作协作的当前任务）", key="chat_mode_task",
                                     placeholder="例如：求证'历史是必然还是偶然' 或 '整理我们学过的三段论知识'")
            if task.strip() or target == "chat":
                set_mode(target, task.strip())
                note = {"chat": "模式已切换回闲聊，大家放松聊！", "proof": f"模式切换为求证讨论！当前议题：{task or '待定'}——开辩！", "work": f"模式切换为工作模式！当前任务：{task or '待定'}——开工！"}
                doubao_speak = chat_space.doubao_speak
                doubao_speak(f"🧭 {note[target]}")
                st.rerun()
        m_cur2 = read_mode()
        st.caption(f"当前状态：**{m_names.get(m_cur2.get('mode'), '💬 闲聊')}**（自 {m_cur2.get('since')} 起）"
                   + (f"｜议题/任务：{m_cur2.get('task')}" if m_cur2.get("task") else ""))
        st.divider()
        # ---- 长期闲聊开关（启动/停止按钮 + 自动恢复） ----
        st.markdown("**🔁 长期自动闲聊（面板开关，开机自动恢复）**")
        from services.long_chat import long_chat_status, long_chat_start, long_chat_stop
        stt = long_chat_status()
        if stt["running"]:
            st.info(f"🟢 长期闲聊运行中（进程 {stt['pid']} · {stt['started_at']} 起 · 事件驱动监听中：有新发言即接龙，10 分钟没人聊自动抛新话题）")
            if st.button("⏹️ 停止长期闲聊", key="long_chat_stop_btn"):
                # 停止最多等 35s，同样放后台，避免卡住 UI
                if start_bg_job("停止长聊", long_chat_stop):
                    st.toast("正在后台停止…")
                else:
                    st.warning("已有后台任务在跑，稍后再试")
                st.rerun()
        elif stt.get("starting"):
            # 2026-11-09：新进程启动要先预热向量库（本机实测 ~14s），这期间 state 还是
            # pid=0。旧逻辑一律显示「已停止」，用户以为没成功就反复点，每点多拉一批进程。
            st.info("🟡 正在启动（新进程要先预热向量库，约 15 秒；完成后这里会变成 🟢，请不要重复点）")
            if st.button("⏹️ 取消启动", key="long_chat_cancel_btn"):
                if start_bg_job("取消启动", long_chat_stop):
                    st.toast("正在取消…")
                else:
                    st.warning("已有后台任务在跑，稍后再试")
                st.rerun()
        else:
            st.info("⚪ 长期闲聊已停止（开启后：事件驱动接龙——有新消息自动接龙，10 分钟没人聊自动抛新话题，全程零轮询等待；重启电脑后若开关为开则自动恢复）")
            if st.button("▶️ 启动长期闲聊", key="long_chat_start_btn"):
                with st.spinner("正在启动..."):
                    long_chat_start()
                st.rerun()
        st.divider()
        # ---- 本轮谁说了什么：只读 chat.md，不碰聊天引擎 ----
        st.markdown("**🗣 本轮谁说了什么**")
        from services.chat_log_stats import (parse as _cparse,
                                             recent_round as _cround,
                                             stats as _cstats)
        _chat_p = Path(config_loader["sandbox"]["root_path"]) / "chat_space" / "chat.md"
        _ents = _cparse(str(_chat_p))
        if not _ents:
            st.caption("还没有聊天记录")
        else:
            _today = time.strftime("%Y-%m-%d")
            _rd = _cround(_ents)
            _st_map = _cstats(_ents, _today)
            _k1, _k2, _k3 = st.columns(3)
            _k1.metric("今日发言", sum(v["today"] for v in _st_map.values()))
            _k2.metric("参与角色", len(_st_map))
            _k3.metric("最近一轮", f"{len(_rd)} 条")
            if _rd:
                st.caption("最近一轮接龙顺序：" + " → ".join(e["who"] for e in _rd))
            st.dataframe(
                [{"角色": v["who"], "今日": v["today"], "累计": v["total"],
                  "最后发言": v["last_ts"],
                  "最后一句": v["last_text"][:80].replace("\n", " ")}
                 for v in sorted(_st_map.values(), key=lambda x: -x["total"])],
                width="stretch")
            with st.expander(f"最近一轮明细（{len(_rd)} 条）"):
                for e in _rd:
                    st.markdown(f"**{e['who']}** · {e['ts']}")
                    _t = e["text"]
                    st.caption(_t[:300] + ("…" if len(_t) > 300 else ""))
        st.divider()
        st.markdown("**聊天记录（最近 30 条）：**")
        for line in chat_space.history(30):
            st.markdown(f"- {line}")

# ========== Tab12 Tutor 教学（已下线） ==========
if _page == 12:
    st.header("🎓 Tutor 教学（已下线）")
    st.caption("Tutor 与 `book_read` 读书能力重叠，面板入口已下线；服务代码保留在 "
               "`services/tutor_service.py`（`ask` / `teach` 仍可直接调用）。"
               "恢复方式见 docs/PANEL_TODO.md，协作登记见 docs/PAIRING.md。")

# ========== Tab13 Obsidian 同步（已下线） ==========
if _page == 13:
    st.header("🗒 Obsidian 同步（已下线）")
    st.caption("依赖本机 vault 路径，不属于核心；面板入口已下线，代码保留在 "
               "`core/obsidian_adapter.py`（`export_sandbox` / `export_all` / `build_index` 仍可直接调用）。"
               "恢复方式见 docs/PANEL_TODO.md，协作登记见 docs/PAIRING.md。")

# ========== Tab14 帮助文档 ==========
if _page == 14:
    st.header("📖 帮助文档")
    st.caption("面板使用说明 + 项目信息（docs/PANEL_HELP.md）")
    _help_path = Path(__file__).resolve().parent / "docs" / "PANEL_HELP.md"
    if _help_path.exists():
        st.markdown(_help_path.read_text(encoding="utf-8"))
    else:
        st.warning("帮助文档缺失：docs/PANEL_HELP.md 不存在")


# ========== 功能插件页（manifest.json 声明了 panel 的已安装插件） ==========
# 页面标题、空状态由插件自己的 panel_render() 负责，这里只兜底失败提示。
if isinstance(_page, str) and _page.startswith("plugin:"):
    _pname = _page[len("plugin:"):]
    _plabel = next((v for k, v in _ALL_PAGES if k == _page), f"插件：{_pname}")
    if not registry.render_panel(_pname):
        st.header(_plabel)
        st.warning(f"插件 `{_pname}` 的面板渲染失败：可能没有 `panel_render()`、模块加载出错，"
                   f"或在「插件管理」页被停用了。")

