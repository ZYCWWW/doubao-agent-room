# agent_project 架构（当前实现）

> **更新：2026-11-08**。本文以**代码现状**为准，是架构的**唯一入口**。
> 2026-11-08 两轮拆除：**猜想库整块删除**（`hypothesis_service` / `hypothesis_cleanup` / 5 个 hypothesis 工具 / `hypothesis_verify` 工具链与权限域）；**空闲调度整块删除**（`core/idle_scheduler.py` / `tools/idle_tool.py` / `services/sandbox_analyst.py`）。沙盘自此只作**缓存区**放临时文件，有视觉工作台承担真正的"看与操作"。下文凡提到这两块的地方均已就地改注或删除；细节见 `docs/PAIRING.md`。
> 此前以设计稿为主的 V2 系列（四份）保留作历史参考，**不要按它们判断实现**——差异见 §10。

---

## 1. 一句话

跑在单人 Windows 机器上的「个人 AI 驾驶舱」：Streamlit 面板（8 页）+ ReAct 主循环 + 视觉工作台（原生窗口嵌入 + 0~1000 舞台坐标）+ 文件沙盘（仅作缓存区）+ Chroma 记忆 + 本地 Ollama 三模型 + 可选 DeepSeek 外部模型 + 桌面桌宠。

规模：**41 个注册工具**，**159 个测试用例**（2026-11-08：猜想拆除 −6、空闲调度拆除 −2）。分目录：root / `core` / `services` / `tools` / `plugins` / `training` / `apps`。

---

## 2. 运行时拓扑

| 进程 / 线程 | 职责 |
|---|---|
| **panel 进程**（`streamlit run dashboard.py`） | 面板 UI；每次交互只执行当前页 |
| ├ 主线程 | 页面路由渲染（侧边栏 `page_router`） |
| ├ periodic 线程 ×4 | `memory_archive`(1h)、`session_cleanup`(5min)、`llm_cache_cleanup`(24h)、`chat_archive`(24h) |
| ├ `hot_loader` 线程 | 5s 扫描插件目录，热加载 |
| ├ `bg_jobs` 线程 | 面板手动耗时任务（闲聊轮 / 游戏轮 / 停止长聊 / 形象设计） |
| └ `async_pool` 线程 | 记忆预热、旧集合迁移等一次性后台任务 |
| **long_chat 进程** | 事件驱动（`ReadDirectoryChangesW`）监听 `chat.md`；启动时预热向量库 + 归档记录 |
| 外部依赖 | Ollama `:11434`、DeepSeek API、（可选）HuggingFace 缓存 |

> 注：venv 的 `python.exe` 实际是 `py.exe` 启动器，所以每个服务在进程表里表现为「启动器 + 解释器」两个 PID——这是正常的，不是重复实例。

---

## 3. 并发不变量（改代码前先读）

| 不变量 | 机制 | 位置 |
|---|---|---|
| 长聊单实例 | 1 字节文件锁，随进程退出释放 | `sandbox/.locks/long_chat.lock` ← `long_chat.acquire_instance_lock()` |
| `chat.md` 单写者 | 线程锁 + 跨进程字节锁；所有写入走同一函数 | `agent_chat._locked_append()` ← `chat_space._log()` |
| 同一时刻只跑一轮闲聊 | 非阻塞轮次锁；抢不到返回 `busy` | `sandbox/.locks/chat_round.lock` ← `agent_chat._acquire_round_lock()` |
| 读进程不建 chroma 集合 | 读路径只 `get_collection`；创建只发生在面板启动 | `chroma_kb.ChromaKB.__init__` / `memory_service.ensure_collections()` |
| chroma 瞬时错误可恢复 | hnsw / segment / sqlite 锁类错误重试 2 次 | `chroma_kb._call_with_retry()` |
| 嵌入模型不再联网 | 检测到本机 HF 缓存即设 `HF_HUB_OFFLINE=1` | `chroma_kb` 顶部 |
| 密钥不进版本库 | 环境变量 → `config/secrets.toml` → `config.toml`（旧明文兼容） | `core/secrets.py` |

---

## 4. 目录

```
agent_project/
├── main.py                 # ReAct 薄调度（react_loop）+ 后台服务注册
├── dashboard.py            # Streamlit 面板（14 页路由，页面代码内联）
├── services/               # 业务门面（18 个）
│   ├── llm_gateway.py      # ★ 唯一 LLM 出口（profile + tag 埋点）
│   ├── agent_chat.py       # 闲聊引擎（身份化上下文 / 摘要 / 轮次锁 / 单写者）
│   ├── long_chat.py        # 长聊守护进程（实例锁 / 事件循环 / 预热 / 归档）
│   ├── memory_service.py   # 四分区记忆 + TF-RI + 迁移 + 下沉 + 访问计数
│   ├── knowledge_service.py# 知识库 + 冲突检测 + 待仲裁队列
│   ├── chat_archive.py     # chat.md 归档 + 摘要指针修正
│   ├── bg_jobs.py          # 面板后台任务
│   ├── vision_workspace.py # ★ 视觉工作台（Tk 原生窗口 + SetParent 真嵌入 + 本地 HTTP 接口）
│   └── …（book / debate / multi_model / prompt_engineer / tutor …）
├── core/                   # 基础设施
│   ├── llm_client.py       # Ollama / OpenAI 兼容 HTTP 封装（重试、信号量、缓存）
│   ├── model_router.py     # main / light / vision / 任务别名
│   ├── chroma_kb.py        # 向量库封装（重试 / 只读打开 / 离线加载）
│   ├── memory/ …           # TF-RI / 注意力 / 反思 / 溯源
│   ├── vision_acl_mgr.py   # 权限唯一权威（config/vision_acl.json，含工作台放行软件）
│   ├── desktop/win32_api.py# ★ 纯 ctypes 的 Win32 层（嵌入/抓图/键鼠注入/窗口枚举）
│   └── …（config / logger / lock / events / metrics / plugin / research …）
├── tools/                  # 41 个工具（plugin_export 协议，含 workspace_* 11 个）
├── plugins/                # installed / market
├── config/                 # config.toml / vision_acl.json / secrets.toml / software_registry.json
├── sandbox/                # 全部运行时数据（见 §6）
├── docs/                   # 本文档 / PANEL_HELP / 历史设计 V2
└── test_suite.py           # 159 个用例
```

---

## 5. 关键链路（5 条）

### 5.1 任务型 ReAct（`main.react_loop`）

`predict_mode` → 首轮召回（skill / fact / 沙盘索引）→ LLM（`profile="deep"`，240s）→ 解析 `Action: 工具 | 参数json` → 模式/风险双检 → 执行工具 → Observation 回灌 → 每轮写沙盘、超 6000 字符压缩 → `FinalAnswer` 或 8 轮上限 → 评分 ≥6 存记忆 + 结论沉淀知识库 + `react_final.md`。

### 5.2 多模型闲聊（`services/agent_chat.py` + `long_chat.py`）

```
chat.md 变化 → 目录事件唤醒 → 抢轮次锁
  → 参与者 = 3 个本地模型 + 小深（DeepSeek，可选）
  → 每人一份 system（身份/形象/口头禅/「哪句是你说的」）
  → 上下文 = 话题 + 摘要（短+长）+ 最近 15 行 + 按事件触发的知识/经验注入
  → 串行接龙（低负载最多 4 人，高负载 1 人）
  → 写入 chat.md（单写者）→ 触发下一轮（15s 冷却，4 轮后休息 60s）
```
摘要：短摘要在批次结束用 qwen3.5:4b 滚动更新（每轮最多吃 40 行）；长摘要在每 50 条用 deepseekR1Q7B 重写（最多 50 行）；两者都按行推进指针，不再丢中间内容。

**教师讲评（2026-11-06 接入）**

| 函数 | 作用 | 条件 |
|---|---|---|
| `chat_space.teacher_material(n=26)` | 从 chat.md 收集最近素材 | 随时可调，纯本地 |
| `chat_space.teacher_review(n=26, teacher="deepseek")` | 云端讲评并发群 | 需 `[external_llm] enabled = true` |
| `chat_space.maybe_teacher_review(min_new=8, min_gap_sec=180)` | 批次收尾自动触发（`long_chat` 在 `count_since_topic >= BATCH_MAX` 时调） | 有外部 LLM 才真讲评，否则只写素材 |

云端关闭时的行为：只把素材落到 `chat_space/teacher_pending.json`，**不发任何云端请求、不耗余额**。
发群统一走 `chat_space.doubao_speak(msg, max_len=…)`；默认 `max_len=300` 会截断长讲评，长文必须传大值。

**名字归属（2026-11-06 调整）**

| 名字 | 归属 | 说明 |
|---|---|---|
| 小深 | 本地 `deepseekR1Q7B:latest` | 逻辑派，爱抠细节 |
| 云端DeepSeek | 云端 `deepseek-chat` | 客人，余额有限 |
| Llama / Qwen / 豆包 | 本地 `llama3.1:8b` / `qwen3.5:4b` / 主 Agent | — |

**坑**：群聊署名走 `core/desktop_characters.meta()` 的 `_MODEL_META`，**不是** `chat_space/characters.json`（后者只管形象、口头禅、介绍）。只改 characters.json 群里不会改名，两处要同时改。

### 5.3 无人值守闭环 —— **已移除（2026-11-08）**

原 `core/idle_scheduler.py` 每 120s tick 执行 `sandbox_summary → verify_hypothesis → generate_hypothesis → explore`。整块删除原因：有视觉工作台之后，想象空间 / 沙盘只作**缓存区**放临时文件，不需要对沙盘做定期 LLM 分析，也不需要自动造猜想。
随之一并删除：`core/idle_scheduler.py`、`tools/idle_tool.py`（`idle_status` / `idle_set` / `idle_trigger`）、`services/sandbox_analyst.py`、事件主题 `idle`、面板页11 的「空闲调度」tab。`config [idle]` 段保留但无读取点。

### 5.4 面板（`dashboard.py`）

侧边栏 `page_router` 选页 → 只执行当前页代码（**8 页**，2026-11-08 从 14 页收敛，见 `docs/PANEL_SCOPE.md`）。耗时操作走 `bg_jobs` 后台线程 + `st.fragment(run_every="2s")` 轮询进度；完成后整页刷新一次。

### 5.4b 视觉工作台（`services/vision_workspace.py`，2026-11-08 新增）

Streamlit 页面没有 HWND，当不了宿主，所以工作台是**独立原生窗口**（Tk）。

```
软件地址管理器(config/software_registry.json) → launch → wait_window 轮询
  → attach: ShowWindow(SW_RESTORE) → 桌面态先改尺寸让应用自己重排
           → SetParent(宿主 stage 的 HWND) → 样式加 WS_CHILD 去 WS_POPUP
           → MoveWindow → SetWindowPos(FRAMECHANGED) → InvalidateRect/UpdateWindow
  → 看见: capture_rect(舞台∩窗口) 截屏 → vl_analyze_image
  → 操作: 0~1000 相对舞台 → to_screen 换算成屏幕坐标 → mouse_event 注入
           （ClipCursor 锁舞台 = 只在这个窗口里操作）
  → detach: SetParent(0) → 还原样式与位置
```

模型侧 `tools/workspace_tool.py` 11 个工具经本地 HTTP（`logs/vision_workspace.port`，8899~8910）转发给工作台，没起时自动用 pythonw 拉起。

**实测坑（都已修）**：

| 现象 | 根因 | 处理 |
|---|---|---|
| 画布上只有点、没有线 | `SetCursorPos` 只改光标位置，不产生 pointer 消息，UWP 收不到拖动 | 拖动改用 `mouse_event(MOVE\|ABSOLUTE)` 逐步注入 |
| 截图抓到的是别的应用 | 工作台被盖住时屏幕抓图抓到遮挡物，真实点击也打到遮挡物 | 截图/动作前 `bring_to_front`（topmost 闪一下再撤） |
| 在舞台里拖笔画线却把软件换掉 | 拖放检测只看「松手点在舞台内」，把内部操作当成从桌面拖入 | 额外要求「按下点在工作台外」才算拖入 |
| 嵌入后 UWP 内容整体偏移、左边缘被裁 | 跨进程 SetParent 后 UWP 的 DWM 表面与窗口矩形不同源 | 不再二次改尺寸；切入前先让应用在桌面态定好尺寸 |
| 宿主销毁时挂死 | 宿主先死、子窗口还嵌着 | `on_close` 先 detach 再 `httpd.shutdown()`，1.5s 后 `os._exit` 兜底。2026-11-08 用 `PostMessageW(WM_CLOSE)` 模拟点右上角 X 实测：宿主 0.6s 就退出（没走到兜底路径），嵌入的计算器被 `SetParent(0)` 还原到原矩形 `(38,38,418,578)`、`parent=0`、仍可见 |
| 拖入舞台却把遮挡物拖进去（实测拖进过豆包客户端） | `_poll_drop` 原先在松手时取「落点最上层的窗口」，桌面有全屏置顶窗口时取到的是遮挡物 | 改成**按下那一刻**记下 `window_at_point` 的 hwnd（拖动中窗口跟光标跑走了，事后回查按下点没有意义），落点窗口只作兜底 |
| `workspace_look` 第二次调用卡满 300s | `_vl_call` 一律用 `vision_timeout=300`（那是冷加载的上限），模型已在显存里也照样等你五分钟 | 先查 `/api/ps` 判断模型是否已加载：热态降到 90s，冷态才用 300s；读超时返回「可能还在冷加载，图片已存好，隔一会儿再调」，不再让调用方干等 |
| 计算器拖进去没反应，还弹框卡住界面 | 计算器是 `ApplicationFrameHost` 托管的 UWP，`SetParent` 被拒；失败分支弹了模态 messagebox，把 Tk 主循环堵死（`/shot`、`/quit` 全进不来） | 拖入与 `/launch` 路径改 `_attach_hwnd(hwnd, auto=True)`，失败只写状态栏与 `agent.log`；实测拖入失败不再弹框 |
| 自建 Tk 计算器嵌进舞台后进程退出、舞台全黑 | Tk 顶层窗口被跨进程 `SetParent` 之后内部假设被破坏，进程直接消失；`/state` 还报着那个死 hwnd | 改用原生 Win32 窗口 + GDI 自绘（`apps/calculator.py`，类名 `AgentCalcWnd`）；同时 `/state` 增加存活校验，窗口没了就清空附着并提示 |
| 点击坐标整体偏移 | `/click` 收的是 0~1000 相对坐标，不是像素；基准矩形是「软件自己的矩形 ∩ 舞台」 | 按 `像素 / 窗口宽高 * 1000` 换算再调，实测 8 连击全部命中 |

2026-11-08 用注入真实鼠标事件补验了「从桌面拖入」这条路径（此前只验过 `/launch` 自动嵌入）：按下点在工作台矩形外、松手点在舞台内时，`_poll_drop` 能正确取到该点窗口并嵌入 —— 画图拖入后 `/state` 返回 `attached=true`、`cls=MSPaintApp`，`/shot` 截到的是完整画图窗口（`sandbox/workspace/ws_20261108_034154_5c1192.png`）。

同一套注入曾暴露一个真问题：`_poll_drop` 原先在松手时取「落点最上层的窗口」，只要有个全屏置顶窗口（比如豆包客户端）压着舞台，取到的就是遮挡物 —— 实测把豆包窗口拖进了舞台。已改成**按下那一刻就记下 `window_at_point` 得到的 hwnd**（拖动过程中窗口跟着光标跑，松手后再回查按下点坐标根本没意义），落点窗口只作兜底；复测 `attached=true / hwnd=9110984 / cls=AgentCalcWnd`，舞台抓图里计算器完整落在左上、5 次点击全命中、显示 `7 + 3 = 10`。

**内置计算器（2026-11-08）**：系统计算器的 UWP 窗口 `SetParent` 必失败，所以自建了一个 —— `apps/calculator.py`，原生 `WNDCLASSW` 窗口（类名 `AgentCalcWnd`、标题「计算器·内置」），`WM_PAINT` 里双缓冲 GDI 自绘，`WM_LBUTTONDOWN/UP` 做命中和按下高亮，`WM_CHAR` 走键盘，求值用 `ast` 白名单（只放行 `Constant` / `UnaryOp` / `BinOp`），不走 `eval`。登记表 `config/software_registry.json` 的「计算器」指向 `apps\calculator.py`，`wa.launch` 对 `.py` 目标会补 `pythonw` 并用项目根作 cwd。实测：`/launch 计算器` → `attached=true / hwnd=15141444 / cls=AgentCalcWnd`，舞台里 8 次千分制点击全 `ok`，`/shot` 返回 380×540，`7+3=` 显示 `10`。

> 踩过的注入陷阱：用 `mouse_event` 脚本复现拖入时，按下点取 `rect[0]+170` 这种「绝对偏移」在窄窗口上是错的 —— 计算器只有 380 宽，170 已经贴近标题栏右侧三个按钮；DPIAware（150%）下按钮区从 `x≈1973` 起，按下点 1970 正好压在「最小化」上，一按窗口就缩了，接着拖的是底下的豆包客户端。复现脚本的按下点一律取**标题栏左侧**（`rect[0]+40, rect[1]+14`）。

**旧视觉链路已下线（2026-11-08）**：面板的「截取当前屏幕 / 执行 VL 识别 / 独立视觉窗口页（`?standalone=vision`）」以及 `browser_read / browser_click / browser_type / browser_press / browser_scroll / browser_search_vision / browser_wait_stable` 七个整屏工具，连同 `tools/browser_vision_tool.py`、`services/browser_vision.py` 一并删除；工具数 55 → 48。整屏 0~1000 坐标的操作语义只保留在工作台（`workspace_*`，坐标相对舞台）。
保留下来的两个易混项：`vl_analyze_image`（VL 识别**函数**，`workspace_look` 与扫描版 PDF 阅读要用，仍是注册工具）、`browser_fetch`（抓网页文本，与视觉无关）。`core/mode/permission.py` 里的 `browser_click_unknown` 是**动作类别**（点未知目标），不是被删的 `browser_click` 工具，故保留原名。


### 5.5 思考型模型必须关思考，否则永远不开口（2026-11-06 修复）

现象：群里 `qwen3.5:4b` 一条话都不说，请求明明返回了。

原因：思考型模型的响应里 `message.content` 是**空字符串**，正文全在 `message.thinking`（`done_reason=length`、`eval_count=80`）。`llm_client` 原来只读 `data["message"]["content"]` → 取到空串 → 上层判定"模型没说话"。

修复两处（`core/llm_client.py`，第 70–72 行与 82–85 行）：

```python
# 请求体（options 之后）
payload["think"] = False        # 关掉思维链；旧版 Ollama 忽略该字段，无害

# 取正文
content = (data.get("message") or {}).get("content") or ""
if not content.strip():         # 空 → 回退 thinking
    content = (data.get("message") or {}).get("thinking") or ""
```

实测：不关思考 → `content=""`、32.9s；关了 → 正常正文、51.3s。**8G 卡上 4B 出一句话约 50s，这是硬件天花板，不是 bug。**

同一时期另一个根因是显存：三模型同驻 `3.6G + 3.9G + 5.4G ≈ 12.9G`，超过 8G 卡，日志全是 `Read timed out`。处理：`ollama stop` 掉 8B 与 R1，只留 4B 驻留。**排障顺序：先看 `ollama ps`，再看是不是思考字段被藏。**

---

### 5.6 思维架构：先选方法，再动手（2026-11-06 接入）

"思维架构"是 `thought/` 下 16 份方法论条目（`react_loop`、`scientific_pipeline`、`multi_world_representation`、`reverse_reasoning`、`bayesian_update`…），`core/thought_lib.py` 负责解析与检索。

**三块零件与真实状态：**

| 零件 | 能力 | 现状 |
|---|---|---|
| `thought_lib.recommend()` | 按任务文本挑方法论 | bigram 打分，`min_score=3` 挡噪声；实测真命中 51 分、误命中 1 分，但"海盗分金币"这类题 0 分 → **认不出题型** |
| `core/research/pipeline.py` | 按七步做（科研求证） | 真按步骤跑、可中断续跑；但 `STEPS` 硬编码，**不读 `thought/`**，与 `scientific_pipeline.md` 是同一套方法的两份实现 |

**注入方式（`main.react_loop`，第 227–252 行）：**

- **门槛**：`_model_can_use_thought(exec_model)` 从模型名取参数量，**小于 7B 整段不注入**（4B 实测见 CHANGELOG 第 13 条）；
- 命中（`score ≥ 3`）：注入该条**步骤骨架**，要求"在 Thought 里按编号逐条作答（每步一行、≤2 句）"——这是**结构模板**，不是"声明用哪个方法"；
- 未命中：注入 `catalog()`——17 行（首行 `none`，后接 16 条），能对上就用其思路，对不上直接推。

**与 ReAct 的关系（重要，别改反）：**

ReAct（`Thought → Action → Observation`）是**主干**，方法论是可选辅助：

- 只注入一次进 system prompt，不接管每轮循环，也不改变 `Action` / `Observation` 格式；
- **形态是"结构模板"而非"声明"**：命中时给的是"按这 6 步逐条作答"的骨架，不要求模型先声明选用哪个方法——实测这类元指令小模型不执行；
- **可以不用**：`none` 是合法选项，方法不适用时直接按 ReAct 推进，不强制走完步骤。

设计意图：塞 600 字全文，模型会当背景略过；给 6 行步骤表并要求逐条作答，才有约束力。小模型则完全不打扰——它连"声明"都不执行，注入只会让它更发散。

**已知缺口（未修，改前先看这里）：**

1. `pipeline.py` 的 `STEPS` 与 `thought/scientific_pipeline.md` 双实现，违反"同一件事只有一处实现"；
2. `core/imagination/validator.py` 的 `contradiction_guidance()` **零调用**，是死函数；
3. `_finish_task` 调 `validate(session_id)` 时**没传 `assertions`**，而矛盾检测是 `if assertions:` 才跑 → 该分支永不执行。

---

## 6. 数据与状态（`sandbox/`）

| 位置 | 内容 | 维护 |
|---|---|---|
| `chat_space/chat.md` | 闲聊流水，**保留最近 2000 行**（`agent_chat._MAX_CHAT_LINES`） | `chat_archive.archive()` 每日 + 每次长聊启动；超限时**直接裁掉最老的**，只靠 archive 兜底 |
| `chat_space/archive/` | 归档的分段流水 | 手动/按需拼回 |
| `chat_space/chat_summary.json` | 短/长摘要 + 行指针（`line` / `long_line`） | 摘要更新与归档时同步修正 |
| `chat_space/*.log` | 长聊/面板日志 | 长聊不含轮转；`logs/agent.log` 按 2MB×3 轮转 |
| `chat_space/{characters,mode,long_chat}.json` | 桌宠形象 / 聊天模式 / 长聊状态 | — |
| `chat_space/teacher_pending.json` | 教师讲评待处理素材（云端 LLM 关闭时累积） | `maybe_teacher_review()` 写，`clear_teacher_pending()` 清 |
| `chat_space/CHARTER.md` / `teacher_notes.md` | 群成员与规矩 / 教过的东西（给后来的模型读） | 手工维护 |
| `hypothesis/` `hypothesis_trash/` | 猜想库（55）/ 回收站（19） | 清洗脚本 + 面板删除 |
| `<session_id>/` | 会话沙盘（react_round / react_final / nodes） | `imaginary_service` |
| `knowledge_pending.json` | 知识冲突待仲裁队列 | 面板「知识库 → 待仲裁」 |
| `agent_memory_db/` | Chroma：旧 4 集合（只读保留）+ 4 分区 | 见 §7 |
| `desktop_queue`（sqlite） | 桌面操作审批队列（持久化） | 面板审批页 |

---

## 7. 记忆与知识

- **分区**：`memory_core`（热，本次迁移 8 条）/ `memory_cold`（3）/ `memory_archive`（0）/ `reflection`（0，暂无调用方）。
- **旧集合** `episode_memory`（11 条）保留只读、已打 `migrated=True`；新写入一律进分区。
- **打分**：`importance + log2(access+1)×0.5 - decay_rate×days`；`is_permanent` 不衰减。
- **访问计数**：检索命中后按所在分区 +1（每进程每条目每小时最多一次）。
- **下沉**：`scan_and_archive` 每小时跑：core→cold（<6）→archive（<2）。
- **知识库**：`knowledge_fact / knowledge_skill / knowledge_rule`；入库前冲突检测，冲突/互补进待仲裁队列。
- **检索闸门**：闲聊只在出现疑问词/专有名词/话题切换时检索；阈值 `dist<0.85`（知识）/`0.75`（经验），软提示「若相关可提及」。

---

## 8. 配置与密钥

| 文件 | 内容 | 说明 |
|---|---|---|
| `config/config.toml` | ollama / models / memory / sandbox / kb / log / dpi / session / vision_acl（idle 段保留但已废弃） | `save_config` 保留未托管段；`[vision.*]` 已移除 |
| `config/vision_acl.json` | `enable_acl` / `rules` / `whitelist` / `path_actions` | **权限唯一权威**，面板 ACL 页维护 |
| `config/secrets.toml` | `[external_llm] api_key` | 已 gitignore；环境变量 `DEEPSEEK_API_KEY` 优先 |

---

## 9. 测试

- `test_suite.py`：**159 用例**，全量约 1~2 分钟，覆盖基础设施、记忆、知识、面板后台任务、闲聊上下文与单写者、锁、ACL、插件、视觉置信度等。
- 面板路由与关键页面用 `streamlit.testing.v1.AppTest` 实测（页面 1/9/11 等）。
- **未覆盖**：真实 Ollama 端到端（依赖模型拉取）、`long_chat` 的事件循环本体、面板其余页面交互。

---

## 10. 与历史设计（V2 系列）的差异

| 历史宣称 | 现状 |
|---|---|
| 11 / 13 个 Tab | **14 页**，侧边栏路由，每次只执行当前页 |
| 三层记忆架构 | **四分区 + 旧集合只读**；TF-RI 与访问计数已生效 |
| `context_compressor` 预算分档 | 仍未接入生产（`_maybe_compact` 按 6000 字符） |
| 事件总线 `events` | 仍只发不收（无订阅者） |
| 节点化沙盘 / 快照 / 分支 | 代码在，仅测试调用 |
| 子 Agent（`crew`） | 仅面板展示状态，无生产发起入口 |
| `toolchain` 类 / `vision.slicer` | 未接入生产 |
| 41 个工具 | **43 个**（新增/删除了工具，以 `ALL_TOOLS` 为准） |

更完整的冗余清单与取舍见 `agent_project_冗余与架构优化建议.md`。

---

## 11. 维护约定（加东西该往哪放）

| 场景 | 做法 |
|---|---|
| 新增工具 | `tools/xxx_tool.py` 定义函数 + `plugin_export`；`main._PLUGIN_SOURCES` 注册；**别写参数校验之外的副作用** |
| 新增面板页 | `PAGE_LABELS` 加一项 + `if _page == N:` 块；重 IO 用 `fragment` / `bg_jobs` |
| 调 LLM | `from services.llm_gateway import ask`，用 `profile` + `tag`，**不要**直接 `llm_client.chat` |
| 写闲聊记录 | `chat_space._log(path, line)`（已带锁），**不要**直接 `open(..., "a")` |
| 写记忆 | `memory_service.add_memory(...)`（分区 + 元数据），**不要**直接操作 chroma |
| 改配置 | `config_loader.save_config()`，新增段要么进 managed，要么确保不被覆盖 |
| 存密钥 | `config/secrets.toml` 或环境变量，**不要**写回 `config.toml` |
| 动并发 | 先看 §3 的锁与不变量，再加锁；锁文件统一放 `sandbox/.locks/` |

---

## 12. 文档导航

| 文档 | 用途 |
|---|---|
| **本文（MASTER）** | 当前实现架构，写代码前看 |
| `docs/PANEL_HELP.md` | 面板逐页使用说明（面板内可查） |
| `USER_MANUAL.md` | 安装、启动、日常使用 |
| `docs\history\ARCHITECTURE_V2*.md` | **历史设计稿**，仅作参考（2026-11-06 已归档到 `history\`） |
| `E:\DS works\agent_project_架构评审.md` | 风险清单 R1–R14 与修复记录 |
| `E:\DS works\agent_project_冗余与架构优化建议.md` | 冗余清单、死代码、P0/P1/P2 与完成情况 |
| `E:\DS works\agent_project_变更清单与回滚指引.md` | 逐批变更、备份、回滚脚本 |
| `docs\ARCHITECTURE_MASTER.md`（本文） | **唯一入口**：架构看这里，别按 V2 系列判断实现 |
| `CHANGELOG.md`（项目根） | 每次改动的六行记录（时间/文件/改了什么/为什么/影响/怎么还原） |
| `training\README.md` | 训练路线：L1 提示词 → L2 数据飞轮 → L3 LoRA |

---

## 13. 训练数据线（`training/`，2026-11-06 建立）

目的：为将来在本机 8G 显存上做 **4B QLoRA** 攒数据，而不是现在就训练。

```
training/
├── README.md              三层路线（L1 prompt/few-shot → L2 数据飞轮 → L3 LoRA）、4B 配方、导回 Ollama
├── demos_batch1~6.json    示范源数据，共 335 段（60/57/56/57/66/39）
├── gen_demos.py           读 batch JSON → 按 §四 格式写进 chat.md；demo_state.json 断点续写防重复
├── clean_chat_demos.py    从 chat.md 移除【训练样本】块（自动备份 chat_backup_<时间戳>.md）
├── prepare_data.py        抽样本 → data/train.jsonl + val.jsonl
└── eval.py                基线评测（长度/空答/复读/关键词重合）
```

**抽样口径**（`prepare_data.py`）：≥4 轮、首条 user 末条 assistant、**助手每条 ≥10 字、用户每条 ≥4 字**。
数据源有三路：chat.md【训练样本】块、`training/demos_batch*.json`、`hypothesis/*.md`。
**关键**：`read_demo_files()` 直接读 JSON，**不依赖 chat.md**——因为 `_MAX_CHAT_LINES=2000` 会把老对话裁掉，曾导致样本数从 439 掉到 324。

**现状**：335 段示范 → 最近一次抽出 324 条（train 292 / val 32）。LoRA 至少 500–2000 条，还差一截。
**硬件结论**：RTX 3070 Laptop 8 GB 只能 4B QLoRA（首选 `qwen3.5:4b`），7B/8B 会 OOM；CUDA + unsloth 环境尚未安装。

---

## 14. 版本管理与变更留痕（2026-11-06 建立）

| 机制 | 位置 | 作用 |
|---|---|---|
| git 仓库 | `E:\agent_project\.git`，分支 `main` | 能看每次改具体哪几行、能一键退回。**纯本地，不推远端** |
| 变动日志 | `CHANGELOG.md` | 每次改动的六行记录 + 回退速查表 |
| 文件快照 | `.backup\<日期>\` | 改前手动复制，git 之外的兜底 |

**进库范围**（`.gitignore` 已定）：源码、配置、文档、题库、章程、笔记进库；`.venv`、日志、`chat.md` 与聊天备份、`sandbox/hypothesis/`（自动生成且持续增长）、`training/data/`、`.backup/` 不进库。当前仓库约 0.44 MB。

**约定**：小改动前先备快照 → 改完立刻记 CHANGELOG 并 `git commit`；大改动（动架构、加服务、改数据结构、删文件）先说，等确认。
