# 面板待完善清单（2026-11-09 复核版）

> 原版 2026-11-08 由小深提出。本版逐条对照代码核实后重写：核销已完成项、订正过期口径、保留仍成立项。
> 口径订正：面板是 **7 个内置页 + 插件自带页**（原写 14 页，已于 11-08 收敛）；测试基线 **159**（原写 165，猜想服务移除后减少）。
> 优先级：P0 = 会误导人 / 死入口；P1 = 好用度；P2 = 锦上添花。

## P0 —— 三条全部关闭（2026-11-09 核实）

| # | 页面 | 原问题 | 现在 | 核实依据 |
|---|---|---|---|---|
| 1 | 监控会话 → 子Agent | 页面永远「暂无子 Agent」，死入口 | **已随页收敛消失** | `dashboard.py` 全库搜 `spawn_subagent` / `暂无子` 零命中；监控页只剩「工具耗时」「模型闲聊」两个 tab |
| 2 | 知识库 | 条目不能改/删/标永久 | **已补齐** | 整页迁入 `plugins/installed/knowledge_plugin/module.py`，带编辑 / 删除 / 设为永久；`dashboard.py` 页 4 路由与条目同时摘除 |
| 3 | 地址管理器 | 填错路径不报错 | **已校验** | `dashboard.py` 页 7 `check_and_create()`：`Path.resolve()` + `mkdir(parents=True)`，四项 `ok1/ok2/ok3/ok3b` 全通过才 `save_config` |

## P1 —— 两条已做，两条待定

| # | 页面 | 原问题 | 现状 | 结论 |
|---|---|---|---|---|
| 4 | 工具耗时 | 进程内累计，重启即清零 | **2026-11-09 已做**：`core/metrics.py` 落盘 `logs/metrics-YYYYMMDD-<pid>.json`，面板加「今日（全部进程）/ 累计（全部历史）/ 本进程」三档 | ✅ 关闭。重启后去掉「本进程」档即可看到历史 |
| 5 | 沙盘文件 | 只看当前 sid，切会话要去第 1 页 | **已做且超出原建议**：`space_plugin` 页首就是「查看哪个会话的沙盘」选择器，还带空状态引导、节点、快照、文件预览 | ✅ 关闭 |
| 6 | 插件管理 | 只能扫描/热加载 | **2026-11-09 已做（本地文件夹版）**：`registry.install(src_dir, install_root, name)` 校验 manifest 后复制进 `plugins/installed` 并注册；`registry.uninstall(name, install_root)` 把目录移到 `plugins/_removed/<名>__<时间>` 再从内存摘掉（随时能捞回）；面板页 10 加「📦 安装插件（从本地文件夹）」与逐插件「卸载」按钮 | ✅ 关闭。**范围限定**：只装本地文件夹，不联网取包；市场源、版本比对、依赖解析仍未做 |
| 7 | 闲聊页 | 不显示「本轮谁说了什么」的结构化结果 | **2026-11-09 已做（只读解析，未动引擎）**：新增 `services/chat_log_stats.py`（`parse` / `recent_round` / `stats`），面板页 11 加「🗣 本轮谁说了什么」——今日发言数、参与角色数、最近一轮条数与接龙顺序、按角色汇总表（今日/累计/最后发言/最后一句）、最近一轮逐条明细 | ✅ 关闭。`services/agent_chat.py`、`services/long_chat.py` 一字未改，chat.md 只读不写 |

## P2

| # | 页面 | 建议 | 现状 |
|---|---|---|---|
| 8 | 全局 | 用 `st.query_params` 记住当前页 | **2026-11-09 已做**：地址栏带 `?p=<页 key>`，新开标签页/重开面板自动回到那一页 |
| 9 | 视觉窗口 | 截图历史（最近 10 张缩略图） | **2026-11-09 已做**：`shot()` 保存后 `prune_shots()` 只留最新 30 张（原先无限增长）；面板页 2 加「🖼 最近截图」5 列缩略图，直接扫 `sandbox/workspace`，不依赖工作台进程在跑 |
| 10 | Tutor / Obsidian | 空状态给示例输入 | ⭕ 已失效。两页 11-09 已下线，只剩说明页，无空状态可言 |

## 2026-11-09 这轮实际改了什么

1. `core/metrics.py` —— 持久化 + 合并
   - `Metrics(persist=False)` 默认零副作用（测试里随便构造，不写盘）
   - 全局单例 `metrics = Metrics(persist=True)`：`record/count` 节流 5 秒落盘，跨日自动翻页并重新载入当日文件
   - 按 `pid` 分文件，面板进程与主进程各写各的，互不覆盖
   - `aggregate(dir, pattern)` 把多进程、多天的文件合并成与 `summary()` 同形状的结果
   - `reset()` 会覆盖写回本进程当日文件，所以「重置」是真清空，不是只清内存
   - 落盘文件里保留 `count / sum_ms`（精确），样本限 `sample_cap=400` 条（p50/p99 用样本近似）——样本被截断后均值仍按真实次数算
2. `dashboard.py` 页 11 —— 「工具耗时」加三档范围切换，显示来源文件数、落盘目录；「重置」按钮改名「重置统计（只清本进程当日）」
3. `dashboard.py` 路由 —— `?p=<key>` 记住当前页
4. `services/vision_workspace.py` —— `SHOT_KEEP = 30` + 模块级 `prune_shots(dir, keep, prefix)`：
   截图保存后按 mtime 只留最新 30 张（此前每调一次 `workspace_look` 就多一张，永不清理）；
   函数容错（目录不存在 / 单个文件删不掉都不抛异常，因为它在截图主链路上）
5. `dashboard.py` 页 2 —— 「🖼 最近截图」5 列缩略图 + 时间戳，空状态给一句怎么产生截图；
   读的是 `sandbox/workspace` 本地目录，工作台没启动也能看历史
6. `core/plugin/registry.py` —— `install(src_dir, install_root, name="")` / `uninstall(name, install_root, trash_root="")` / `_forget(name)`
   - install：校验 `manifest.json`（name/version/desc）与 `module.py` 存在 → 目录名即插件名 → `copytree`（跳过 `__pycache__`/`*.pyc`）→ `discover` → 状态不是 enabled 就如实报出来
   - **不覆盖**：同名目录或注册表里已有同名插件都直接拒绝，重装必须先卸载
   - uninstall：只认 `_FilePlugin`；`os.path.commonpath` 确认目录确实在安装根下（不同盘符会抛，已兜住）；移动到 `plugins/_removed/<名>__<时间>` 而不是删除，然后 `_forget` 摘掉 `_plugins/_status/_tool_index`
   - 内置插件（`_BuiltinPlugin`）与市场目录里的插件都不给卸
7. `dashboard.py` 页 10 —— 「📦 安装插件（从本地文件夹）」expander（源路径 + 可选改名 + 安装按钮），
   每个 `source == "local"` 的插件多一个「卸载」按钮；操作结果走 `session_state` 跨 `st.rerun()` 显示（同帧显示会被 rerun 冲掉）
8. `services/chat_log_stats.py`（**新文件**）—— 聊天流水只读解析
   - `parse(path)`：按 `时间 | 说话人：内容` 切条，**无时间前缀的行并入上一条**（长发言本来就跨行，
     按行显示会把一段话拆成好几条）
   - `recent_round(entries)`：「一轮」用"从末尾往前、直到说话人重复"定边界，每人一次
   - `stats(entries, day)`：按说话人汇总今日 / 累计 / 最后发言 / 最后一句
   - **完全不 import 聊天引擎的任何东西**，也不写 chat.md
9. `dashboard.py` 页 11 「模型闲聊」—— 加「🗣 本轮谁说了什么」：三个指标 + 按角色汇总表 +
   最近一轮逐条明细；顺手把两处 `st.dataframe(use_container_width=True)` 改成 `width="stretch"`
   （Streamlit 1.66 已提示前者 2025-12-31 后移除）
10. 测试卫生 —— 清掉回归输出里长期漂着的两类 ResourceWarning（**不是清单项，是自查自发现**）
   - `core/vision/slicer.py` / `confidence.py` / `stability.py` 共 6 处 `Image.open(` 全改 `with`
   - `core/desktop/queue.py` 的 `_conn()` 改成 `@contextlib.contextmanager`（commit / rollback / `finally: close()`）：
     `sqlite3.Connection` 当上下文管理器时**只提交不关闭**，6 个方法每调一次漏一个句柄，
     调用方的 `with` 写法一行没动
11. 科研七步性能 —— 196.4s 那一步的真因是**显存换入**，不是上下文长（**也是自查自发现**）
   - 实测：8GB 显存（已用 7961/8192 MiB）同时驻留三个模型，`llama3.1:8b` 的 `size_vram` 只有 3.67/4.59GB
     （一半在 CPU）；短 prompt 总耗时 85.88s 里 **load_duration 85.67s**，推理仅 2 tokens
   - `deepseekR1Q7B` 的 `think=false` 在 ollama 0.40.13 **不生效**：content=0 字符、349 字全落 `thinking`
     且被 `num_predict` 截断 → 七步第 3/6/7 步用它必然拿不到 JSON
   - `core/research/pipeline.py`：七步统一走 light + `timeout=240` + `num_ctx=8192` + `tag="research"`
     （12288 时 8b 装不进显存；light/main 交替每年换入 20~25s，七步要切 5 次）
   - `core/llm_client.py`：读超时只重试 1 次（连接重置/5xx 仍 3 次），
     成功响应里 `load_duration/total_duration > 0.4` 打 warning
   - 实测对比：第 2 步 **367.8s / success=False** → **122.0s / success=True**，
     且外部资料真被采纳（`research_object` 从「未明确」→「火报回路板」）
12. 模型驻留上限（**环境层改动，不改代码**）—— `OLLAMA_MAX_LOADED_MODELS=2`（User 级环境变量）
   - 改前 `OLLAMA*` 环境变量一个都没设，三个模型同时挤 8GB；改后 `/api/ps` 任何时刻最多 2 个，
     第三个进来时最旧的被驱逐
   - 实测：`llama3.1:8b` 换入 **85.67s → 52.7s**；`qwen3.5:4b` 从「3.66/2.26 半驻留」变成可完整驻留
   - 附带一条更硬的结论：**`num_ctx` 是显存的直接杠杆**。同一个 8b，12288 窗口要 7.26GB
     （vram 只拿到 6.5），8192 窗口只要 **5.82GB 且 `size == size_vram`**（100% 进显存）
   - 代价：两个大模型（8b 5.82 + deepseek 5.64 = 11.46GB）仍超 8GB，共存照样换入；
     想零换入只能让一条流程只用一个模型——七步就是这么做的
   - 回归 **159/159 OK**（93.139s，ResourceWarning 0）

**未 commit**，全部在工作区。回归 **159/159 OK**（146.5s，与 AppTest 并行跑时测得）。

## 给豆包的两条约定

1. **动面板前先跑 `python test_suite.py` 记基线（当前 159）**，改完再跑一次；面板改动至少用 AppTest 把改到的页渲染一遍。
2. **别直接改 `sandbox/chat_space/chat.md` 的结构**——它按行 `时间 | 说话人：内容` 解析，插入标题或分隔线会被当成上一条的续行。

## 已经做完、别重复做

- 面板页路由（每次只执行当前页；7 内置 + 插件页）
- 闲聊三个按钮后台化 + 进度 fragment
- 工具耗时页数据结构修复（stages / counters）
- 知识库「待仲裁」区 + 编辑 / 删除 / 设为永久
- 提示词模板保存 / 删除
- ACL 页 6 个开关
- 沙盘文件会话选择器
- 工具耗时落盘与三档切换（2026-11-09）
- 页面路由 `query_params` 记忆（2026-11-09）
