# Agent 使用手册

## 前置依赖
1. Ollama 本地服务：`http://127.0.0.1:11434`
2. 已拉取模型：`deepseekR1Q7B:latest`（推理）、`llama3.1:8b`（快速/工具）、`qwen3.5:4b`（视觉/摘要）
3. 依赖安装：`pip install -r requirements.txt`

## 启动方式

| 用途           | 命令                                                                            |
| ------------ | ----------------------------------------------------------------------------- |
| 桌面图标（最省事） | 双击桌面「Agent 控制面板」：已在运行则只开浏览器，没运行则自动起服务（约 5 秒） |
| 面板             | `streamlit run dashboard.py --server.headless true` → <http://localhost:8501> |
| 命令行 ReAct    | `python main.py`                                                              |
| 长期闲聊（面板里可开关） | 面板「监控会话 → 模型闲聊 → ▶️ 启动长期闲聊」，或 `python -m services.long_chat`                  |

> 面板与命令行共用同一套配置、沙盘、知识库与审批队列；审批队列已持久化（sqlite），重启不丢。

## 面板（14 页，侧边栏切换）

| 页面           | 用途                                                                   |
| -------------- | ---------------------------------------------------------------------- |
| 🧭 Agent调度    | 输入任务跑 ReAct，可选执行模型与轮次                                   |
| 🪟 视觉工作台   | **独立原生窗口**：把桌面软件嵌进舞台，只在舞台内看图与操作（旧的整屏截屏 + 浏览器视觉六工具已下线） |
| ✅ 审批队列     | 桌面键鼠/打开文件等操作人工批准                                        |
| 📚 知识库       | 检索三集合；查看并处理「⚖️ 待仲裁知识」                                 |
| 🔐 视觉ACL      | **权限唯一入口**：键鼠/读写/启动/扫描开关 + 白名单                     |
| 🗂 沙盘文件     | 浏览当前**会话**的沙盘 md                                              |
| 🌐 地址管理器   | 修改沙盘/知识库/日志路径（改后需重启）                                 |
| ✍️ 提示词助手   | 优化提示词；可保存/删除模板                                            |
| 💡 猜想研究     | 猜想库 / 科研七步；论据、删除（回收站）                   |
| 🧩 插件管理     | 已安装/市场插件，热加载                                                |
| 📡 监控会话     | 模式切换、空闲调度、子 Agent、工具耗时、**模型闲聊**（含桌宠形象设计） |
| 🎓 Tutor教学    | 分步教学 / 大纲生成                                                    |
| 🗒 Obsidian同步 | 沙盘导出到 vault + 索引                                                |
| ❓ 帮助文档     | 渲染 `docs/PANEL_HELP.md`                                              |

## 记忆架构
- **知识库**：`knowledge_skill` / `knowledge_fact` / `knowledge_rule`（长期固化）
- **情景记忆**：四分区 `memory_core` / `memory_cold` / `memory_archive` / `reflection`，按 TF-RI 打分并逐级下沉；旧 `episode_memory` 只读保留
- **想象沙盘**：每个会话的 md 草稿（推理轮次、结论、截图说明），会话隔离

## 视觉工作台（把软件拖进一个窗口里操作）
面板第 2 页「🪟 启动视觉工作台」会拉起一个独立原生窗口，左侧是**软件地址管理器**（`config/software_registry.json`）和桌面窗口列表，右侧是**舞台**。

- **让模型操作某个软件**：点左侧软件名「启动」，它会自动嵌进舞台；或把桌面上已开的窗口直接拖进舞台。
- **看见**：`workspace_look` 只截舞台这一块，交给 VL 识别，返回 0~1000 相对坐标。
- **操作**：`workspace_click / workspace_drag / workspace_type / workspace_key / workspace_scroll` 都是**坐标系相对舞台**，执行期间用 `ClipCursor` 把光标锁在舞台范围，等于「只在这个窗口里操作」。
- **求证画图**：`workspace_open("画图")` → `workspace_look(...)` 看清工具栏 → `workspace_drag(...)` 画线 → 再 `workspace_look(...)` 回看。截图落在 `sandbox/workspace/`。
- **放行控制**：「视觉ACL」页的「视觉工作台放行软件」勾选哪些软件能进舞台；ACL 关闭时不校验。
- **哪些能拖进去**：自己就是顶层窗口的软件 —— 记事本（`Notepad`）、画图（`MSPaintApp`）、内置计算器（`AgentCalcWnd`）。**拖不进去的**：由 `ApplicationFrameHost` 托管的 UWP，例如系统计算器、设置、照片。它们是"寄宿"窗口，不允许多挂一个父窗口，`SetParent` 会被拒；拖进去后舞台不变、状态栏提示失败，**不会弹框卡住**。
- **内置计算器**：系统计算器属于上面那类嵌不进去的 UWP，所以另做了一个 —— `apps/calculator.py`（原生 Win32 窗口 + GDI 自绘，类名 `AgentCalcWnd`、标题「计算器·内置」）。登记表里「计算器」指向它，点「启动」会自动嵌进舞台；四则、百分号、取负、退格、键盘输入都可用，求值走 `ast` 白名单不走 `eval`。要跑它：`.venv\Scripts\pythonw.exe apps\calculator.py`（登记表写相对路径，工作台会用项目自带 pythonw 拉起）。
- **拖进去之后鼠标点不动/画面空**：`/state` 会校验已嵌窗口是否还活着，进程退出会自动清空附着并提示「窗口已经消失」，不会再报一个假的 `attached=true`。
- **注意**：工作台被别的窗口盖住时，截图与点击都会落到遮挡物上，所以动作前会自动把工作台提到最前（会抢焦点）。
- **`workspace_look` 慢怎么办**：VL 超时按「视觉模型是否已加载」分两级 —— 冷加载最多等 `vision_timeout`（`config.toml`，默认 300s），模型已在显存里则只等 90s。若真的超时，会直接告诉你「可能还在冷加载、图片已保存、过一会儿再调一次」，不用重新截图。

> **旧的整屏链路已下线（2026-11-08）**：「截取当前屏幕 + VL 识别」那套 UI，以及 `browser_read / browser_click / browser_type / browser_press / browser_scroll / browser_search_vision / browser_wait_stable` 七个整屏工具，全部删除，一律改用上面的工作台。
> 保留的是 VL 识别**函数** `vl_analyze_image`（`workspace_look` 与扫描版 PDF 阅读要用它）和 `browser_fetch`（抓网页文本，与视觉无关）。

## 联网检索
主循环现在有 `web_search` 工具（必应，国内直连，不需要 API key）：模型遇到「库里没有的现实信息、时事、价格」这类问题可以真的去查，返回「标题 + 一句摘要 + 域名」。**不给正文**是故意的 —— 本地小模型读不完长文，也容易把原文整段复述出来。

- 群聊链路（`services/agent_chat`）本来就在用同一套检索，靠识别发言里的 `web_search("关键词")` 或小模型自己长出的 `🌐 "关键词"` 来触发；这次只是把同一个能力也接进了 ReAct 主循环。
- **局限**：中文的天气、股价这类需要结构化数据源的查询，必应摘要经常给不到，会返回百科或新闻页；这时别硬信第一条。检索不到网络或关键词太窄时，工具会明确回「没搜到条目」。
- 底层实现 `core/web_search.py`（`search()` / `format_results()`），工具层只是 `tools/web_search_tool.py` 的一层薄封装。

## 安全
- 桌面键鼠与打开文件等动作进入审批队列，人工确认后执行
- 文件/应用访问受 `config/vision_acl.json` 白名单 + 动作级规则约束
- 外部模型密钥放 `config/secrets.toml` 或环境变量 `DEEPSEEK_API_KEY`，**不要**写回 `config.toml`

## 常见问题
- **改了路径不生效**：地址管理器的改动需重启面板与 `main.py`
- **面板某页卡住**：耗时的闲聊/形象设计已转后台，进度在页面内显示；若仍卡，看 `sandbox/chat_space/panel_err.log`
- **键鼠被拒**：到「视觉ACL」页确认 `allow_mouse_keyboard` 已勾选
