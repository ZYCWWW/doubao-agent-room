# 面板收敛方案（提案 · 待评审）

> 2026-11-08，豆包B 起草，**主人已选方案 B，并于当日执行完毕**（执行记录见文末第八节）。
> 主人原话：「把视觉工作台留下，其他功能去除吧，其他功能以后都插件形式加入，现在把重要的几个留下就行。」
> 补充澄清：「聊天这些不包括」——**聊天 / 小屋 / 长聊引擎完全不在本次范围内，一行没碰。**

## 一、范围边界（先说清不碰什么）

明确**不动**：

- `services/agent_chat.py`（1418 行，聊天引擎/说话人/模式）
- `services/long_chat.py`（520 行，长期自动闲聊）
- `sandbox/chat_space/chat.md` 及三个锁（`long_chat.lock` / `chat_append.lock` / `chat_round.lock`）
- 面板「监控会话 → 模型闲聊」那个 tab（它是聊天展示，不是聊天本体）
- 视觉工作台本体 `services/vision_workspace.py`（803 行）

本次只处理**面板页签的收与放**。

## 二、现状盘点（14 页 → 代码分布）

| # | 页签 | 面板代码行数 | 背后模块 | 生产调用 | 判断 |
|---|---|---|---|---|---|
| 1 | 🧭 Agent调度 | 37 | `main.react_loop` / `ALL_TOOLS` | 主循环 | **核心骨架** |
| 2 | 🪟 视觉工作台 | 46 | `services/vision_workspace.py`（803） | 有 | **主人点名保留** |
| 3 | ✅ 审批队列 | 16 | `GLOBAL_QUEUE` / `execute_approved` | 有（视觉动作审批） | 与页5 ACL 重复入口 |
| 4 | 📚 知识库 | 28 | `services/knowledge_service.py`（326） | 有 | ⚠ **豆包正做 P0-2** |
| 5 | 🔐 视觉ACL | 54 | `core/vision_acl_mgr` | 有 | **视觉工作台的安全开关，配套保留** |
| 6 | 🗂 沙盘文件 | 14 | 文件浏览 | 弱 | 低频查看 |
| 7 | 🌐 地址管理器 | 48 | `config_loader` / `save_config` | 有 | ⚠ **豆包正做 P0-3** |
| 8 | ✍️ 提示词助手 | 60 | `services/prompt_engineer.py`（147） | 弱 | 低频 |
| 9 | 💡 猜想研究 | 118 | `hypothesis_service`（245，**已删**）/ `imaginary_service`（116，保留）/ `core/research`（保留） | 弱 | **已整块删除（2026-11-08）** |
| 10 | 🧩 插件管理 | 18 | `core/plugin` registry | 有 | **未来插件入口，必须留且要加强** |
| 11 | 📡 监控会话 | 177 | `mode` / `idle_scheduler` / `bg_jobs` / `metrics` / 闲聊 tab | 有 | **运行状态可见，"能跑"的证据** |
| 12 | 🎓 Tutor | 7 | 已下线（今日） | 无 | 已下线 |
| 13 | 🗒 Obsidian | 7 | 已下线（今日） | 无 | 已下线 |
| 14 | ❓ 帮助文档 | 10 | `docs/PANEL_HELP.md` | — | **迁移说明的落点** |

## 三、两个方案

### 方案 A（激进：6 页）
保留 `1 Agent调度 / 2 视觉工作台 / 5 视觉ACL / 10 插件管理 / 11 监控会话 / 14 帮助文档`，
其余 8 页全部下线（3 / 4 / 6 / 7 / 8 / 9 / 12 / 13）。

- 优点：最干净，一屏就能看完，符合"只留重要的几个"。
- 代价：**豆包手上的 P0-2（知识库编辑/删除/永久）和 P0-3（地址管理器路径校验）直接作废**，
  发布门槛里「P0 全部关闭」那两条要么删掉，要么改成"插件化之后再做"。

### 方案 B（稳：8 页）——**推荐**
在方案 A 基础上**额外保留 4 知识库 / 7 地址管理器**，只下线 3 / 6 / 8 / 9 / 12 / 13。

- 理由：这两页正是豆包正在改的 P0，砍了等于让她的活白干、还要改发布门槛；
  而且知识库是"记忆能查能改"的对外可见面，地址管理器是唯一的路径配置入口（砍了就得手改 config.toml）。
- 代价：比 A 多两个页签。

> 无论 A 还是 B，`3 审批队列` 的下线方式都是**并入页2 视觉工作台**（同一套队列/ACL，不该分两个入口看），
> 而不是简单删掉——否则待审批动作就没有地方点了。

## 四、"去除"的技术含义（不删代码）

沿用 PAIRING 已定的原则：**入口先下线，代码保留一版**。

具体三步（每页约 5 行改动）：

1. 从 `PAGE_LABELS` 里摘掉该页 → 侧边栏不再出现；
2. 该页的 `if _page == N:` 段整段替换为一句说明（页面不存在就不会走到，但留注释便于回滚）；
3. 背后的 `services/*.py`、`core/*.py` **一行不删**。

## 五、"以后以插件形式加入"——现在缺什么

> **✅ 2026-11-09 已实现，本节从"缺什么"变成"怎么做"的记录。** 实际落地方案比下面这条设计更薄：
> `manifest.json` 写 `"panel": {"label": "🔬 科研七步", "order": 40}`（没做 `entry` 字段，
> 约定模块里有个叫 `panel_render()` 的函数即可）；`core/plugin/registry.py` 新增
> `list_panels()`（按 enabled 过滤）和 `render_panel(name)`（懒加载 + 取 `panel_render` + 异常兜底）；
> `dashboard.py` 把插件页拼进 `_ALL_PAGES`，用 `"plugin:<name>"` 字符串键区分内置整数键。
> 试点是「科研七步」：`core/research/pipeline.py` 留在原地，`plugins/installed/research_plugin/module.py`
> 只做壳（3 个工具 + 整页面板）。工具总数 43 不变，回归 159/159，AppTest 9/9 页全过。

原设计（保留作历史对照）：

- `plugins/installed/<name>/manifest.json` 增加可选字段 `"panel": {"label": "…", "entry": "module:render"}`；
- 面板启动时扫一遍已安装插件，把声明了 `panel` 的注册进 `PAGE_LABELS`，点击时调 `render()`；
- 预计 80~120 行，改动集中在 `dashboard.py` 的路由段 + `core/plugin.py` 的读取函数。

**建议顺序**（已执行）：这轮只做收敛（下线页签），页面插件机制留到下一轮单独做——
否则一边砍一边建，两边都做不干净。

## 六、需要两位表态的点

1. **小深**：方案 A 还是 B？页3 并入页2 的做法你认可吗？
2. **豆包**：如果 A，你手上的 P0-2 / P0-3 怎么办（作废 / 转插件 / 顺延）？
3. 两位是否同意「页面插件机制」放下一轮，这轮只收敛？

## 七、验收（无论 A/B）

- `python test_suite.py` → 与基线一致（当前基线见 `docs/PAIRING.md`）
- AppTest 逐页渲染保留下来的页，无异常
- 侧边栏实际只剩目标页数，被下线页点不进去
- `docs/PANEL_HELP.md` 同步改：把下线页写成「已下线，见 docs/PANEL_SCOPE.md」
- `git status` 里只应出现 `dashboard.py` / `docs/PAIRING.md` / `docs/PANEL_HELP.md` / 本文件

## 八、执行记录（方案 B，2026-11-08，豆包B）

**结果：14 页 → 8 页**（`dashboard.py`）

保留：`1 Agent调度` / `2 视觉工作台` / `4 知识库` / `5 视觉ACL` / `7 地址管理器` / `10 插件管理` / `11 监控会话` / `14 帮助文档`

下线：`3 审批队列`（**并入页2 视觉工作台底部「✅ 待审批的桌面操作」**）/ `6 沙盘文件` / `8 提示词助手` / `9 猜想研究` / `12 Tutor` / `13 Obsidian`

> **状态更新（2026-11-09）**：下线页里 `6 沙盘文件` → 以「📝 想象沙盘」插件页回来；`8 提示词助手` → 以「✍️ 提示词助手」插件页回来；另外新增「🔬 科研七步」「📖 阅读」「🌐 联网」「🗣️ 辩论现场」四个插件页。侧边栏现为 **14 页**（内置 8 + 插件 6）。`9 猜想研究` / `12 Tutor` / `13 Obsidian` 仍维持下线。

改动方式：

- `PAGE_LABELS` 删 6 项，**页码保留原值不重排**（路由按 label 反查 key，不连续无影响，零风险）
- 页3 的 15 行审批逻辑原样搬进页2 末尾，`GLOBAL_QUEUE` / `execute_approved` 复用文件顶部已有的 import
- 页6 / 8 / 9 的 `if _page == N:` 改为 `if False:` —— 视图代码留一版、永不执行
- 背后 `services/*.py`、`core/*.py` **一行未删**

验收实测：

| 项 | 结果 |
|---|---|
| `py_compile dashboard.py` | ok |
| 侧边栏页数 | **8**，与目标 8 页逐项一致，无缺无多 |
| AppTest 逐页渲染 | 8 页全部 `exception=none`，标题正常 |
| `python test_suite.py` | **167/167 OK**（回归基线未动） |
| 文档 | `PANEL_HELP.md` 页签表已标记 6 页下线 / 页3 并入说明 |

**未做（留给下一轮）**：页面插件机制（第五节）—— **已于 2026-11-09 完成**，见第五节顶部说明，试点为科研七步插件。

## 九、后续：猜想研究页已从「if False」变成真正删除（2026-11-08）

主人对沙盘分析 / 猜想库提出了完整设计，看完后判定「太复杂」，改成**整块拆除猜想相关功能**。
`docs/HYPOTHESIS_DESIGN.md` 的重构提案作废（未执行任何设计）。

| 项 | 动作 |
|---|---|
| `dashboard.py` | 删 hypothesis 导入 3 行；Tab9 猜想研究 118 行**整段切除**（不再是 `if False` 死代码）；页11 空闲调度 tab 收敛为「沙盘分析」单按钮 |
| `services/hypothesis_service.py`（246 行） | **删除** |
| `services/hypothesis_cleanup.py` | **删除** |
| `tools/research_tool.py` | 删 5 个 hypothesis 工具（create/list/update/delete/evidence），留 3 个 research 工具 |
| `core/toolchain.py` | 删 `hypothesis_verify` 内置链 |
| `core/mode/permission.py` | 删 3 条工具→域映射 + `hypothesis_verify` 权限域 |
| `core/idle_scheduler.py` | 删 `_verify_one_hypothesis` / `_generate_new_hypothesis` / `_explore`，`status()` 去掉 verify/generate/explore 计数 |
| `services/sandbox_analyst.py` | 删 `create_hypothesis(h, h)` 自动造猜想分支 |
| `services/knowledge_service.py` | 删 `hypothesis_verified` 来源权重 |
| `test_suite.py` | 删 `TestHypothesis`、`TestHypothesisTimeoutRevert` 共 6 个测试 |
| 数据 | 106 个文件（78 + 23 + 5）移入 `_archive/hypothesis_removed_20261108/` |
| **保留** | `services/imaginary_service.py`（想象沙盘本体）、`core/research`（科研七步，不依赖猜想） |

验收实测：

| 项 | 结果 |
|---|---|
| `py_compile` 9 个改动文件 | ok |
| AppTest 逐页渲染 | **8 页全部 ok，无异常**（含页11 监控会话的 t112，此前必 KeyError） |
| `python test_suite.py` | **161/161 OK**（原 167 − 6 个猜想测试） |
| 面板重启 | 旧进程 28052/26728 已停，新进程起，8501 端口返回 **200** |
| 侧边栏 | 8 页，与收敛后一致 |

## 十、空闲调度整块拆除（2026-11-08）

主人定调：**有了视觉工作台，想象空间 / 沙盘只是缓存区，放临时文件用；不需要空闲调度。**

| 项 | 动作 |
|---|---|
| `core/idle_scheduler.py` | **删除**（原 120s tick：沙盘分析 → 求证 → 生成 → 探索） |
| `tools/idle_tool.py` | **删除**（`idle_status` / `idle_set` / `idle_trigger` 三个工具） |
| `services/sandbox_analyst.py` | **删除**（沙盘定期分析 → `conclusion_*.md`，其唯一调用方就是上面两者） |
| `main.py` | 去 `("idle", idle_plugin)` 注册、去 `idle_scheduler.start()`；后台服务注释同步 |
| `dashboard.py` | 页11 tabs **5 → 4**，「空闲调度」tab 整块删除；顶部「后台服务」指标去掉空闲调度 |
| `core/events.py` | 事件主题表去掉 `idle` |
| `test_suite.py` | 删 `TestSandboxSessionScan`（2 个测试，测的正是 `sandbox_analyst._is_session_dir`） |
| `config/config.toml` | `[idle]` 段**保留但已废弃**（加注释，代码无读取点） |
| **保留** | `services/imaginary_service.py`、`tools/space_tools.py`（沙盘本体，改作缓存区用途） |

验收实测：

| 项 | 结果 |
|---|---|
| `import main` | 通过，`ALL_TOOLS` **44 → 41** |
| 被删三模块 | `core.idle_scheduler` / `tools.idle_tool` / `services.sandbox_analyst` 全部 `ImportError` |
| AppTest 逐页渲染 | **8 页全部 ok，无异常** |
| `python test_suite.py` | **159/159 OK**（原 161 − 2 个沙盘扫描测试） |
| 面板重启 | 8501 端口 `open=True`、HTTP **200** |

**聊天 / 小屋 / 长聊引擎按主人要求一字未改。**

## 十二、知识库页迁入插件（2026-11-09）

第八个插件。与前面七个同一范式：业务引擎原地不动，插件只做「工具注册 + 整页面板」的外层封装。

| 项 | 动作 |
|---|---|
| `plugins/installed/knowledge_plugin/manifest.json` | 新建。`panel={"label":"📚 知识库","order":90}`；`config.defaults.enabled=true` |
| `plugins/installed/knowledge_plugin/module.py` | 新建。`plugin_export = {}`（**零工具**，知识库从未注册过模型工具）+ 无参 `panel_render()` 整页 |
| `core/chroma_kb.py` | 新增 `delete_document(coll, doc_id)`（只接受明确 id）与 `refresh_collections()`（多进程索引快照刷新） |
| `dashboard.py` | 页 4 整段（28 行）替换为迁移注释；`PAGE_LABELS` 删 `4: "📚 知识库"`（现 7 键）；删 `from services.knowledge_service import knowledge_service` |
| `dashboard_apptest.py` | `_page_labels()` 内置 8 项 → 7 项 |
| `services/knowledge_service.py` | **一行未动** |

**页数**：内置 7（1 / 2 / 5 / 7 / 10 / 11 / 14）+ 插件 7（30 / 40 / 50 / 60 / 70 / 80 / 90）= **14 页**，
「📚 知识库」由内置第 3 位移到插件页末位。工具总数仍 **43**（内置 24 + 插件 19），因为本插件零工具。

**两处与预估不符（开工前核实）**：

1. 页 4 实际只有 **28 行**（不是此前记的 100+ 行），内容为「检索输入 + 四集合 radio + 结果循环 + ⚖️ 待仲裁三列按钮」。
2. **业务层原本完全没有删除能力** —— `knowledge_service.py` 与 `core/chroma_kb.py` 都只有 `add_document` / `update_document` / `query`，
   所以「🗑 删除」这一项必须先在 `chroma_kb` 补 `delete_document`，不能只靠面板调现成方法。

**「♾ 设为永久」的诚实判断**（本轮最有价值的核实）：

Grep 全库 `permanent|decay|archive_old|memory_archive|memory_cold` 得 41 处，逐条核对后确认
`is_permanent` 的**唯一消费者**是 `services/memory_service.py` 的 `scan_and_archive`，
它只遍历 `memory_core → memory_cold → memory_archive`（`plan` 只有这两对），遇 `is_permanent` 就 `continue`。
而 `knowledge_fact` / `knowledge_skill` / `knowledge_rule` / `episode_memory` **不参与自动归档**
（`episode_memory` 在 docstring 里明确「不再作为写入/归档目标」）。
**结论：在这四集合上写 `is_permanent` 是没人读的死标记 = 假功能，所以面板只在 `memory_core` / `memory_cold`
的结果上给「♾ 设为永久」按钮，其余集合在页末用 caption 说明原因。** 这一条已写进 `module.py` 文件头。

**验收实测**（浏览器 + AppTest + 回归三层）：

| 项 | 结果 |
|---|---|
| 侧边栏 | **14 页**，末项「📚 知识库」；知识库页首屏正常（标题 + caption + 8 集合条数行 + 检索区） |
| 检索 | `knowledge_fact` 搜「插件验收」命中 **2 条**；`memory_core` 搜「猜想结论」命中 **5 条** |
| ✏️ 编辑 | 展开正文框 → 追加文本 → Ctrl+Enter → 「💾 保存修改」→ 绿框「已保存：7398b91b-…（固定 45 字）」，元数据多出 `edited_ts` |
| 🗑 删除 | 黄框「确认删除这条？删除后无法恢复。id: 7398b91b-…」→「确认删除」→ 该条消失、命中 2 → 1、`knowledge_fact` 回到原始 `d3fb5fad` 一条 |
| ♾ 设为永久 | 仅 `memory_core` / `memory_cold` 显示。`memory_core` 第 1 条点击后盘上 `is_permanent` 变 `True`；**验收后已脚本恢复为 `False`**（原值），其他字段与正文长度均未变 |
| 非分区集 | `knowledge_fact` 结果上只有「编辑 / 删除」两列 + caption「该集合不参与自动归档…不写了也没人读」 |
| 待仲裁 | 「⚖️ 待仲裁知识（冲突 / 互补）」共 **19 条待处理**，逐条给「采用新知识 / 丢弃新知识」 |
| `py_compile` | 4 个改动文件全 ok |
| 注册表 | `list_panels()` 7 条，顺序 30/40/50/60/70/80/90；9 个插件全 `status=enabled`；`all_tools()` 仍 19 |
| `dashboard_apptest.py` | **14/14 页通过**（目标 14、实际 14，逐页 `exception=none`） |
| `python -m unittest test_suite` | **159/159 OK**（173.1s，基线未动） |

**本轮抓到并修掉的两个真问题**：

1. **chromadb 多进程索引快照**：面板检索「插件验收」只显示 1 条，直接脚本调同一 `search_knowledge` 返回 2 条，
   而条数行明确写着 `knowledge_fact：2`。原因是多进程共用同一 persist 目录时，各进程的 `Collection` 对象各持一份索引快照 ——
   `count()` 走 sqlite 看得到新行，`query()` 却看不见别进程写入的向量。修法：新增 `ChromaKB.refresh_collections()`，
   在 `_coll_names()` 开头先调一次，丢弃本进程的快照。重启后同一检索命中 2 条。
2. **「同一行显示两遍」是浏览器扩展，不是渲染 bug**：修好索引后结果上方出现两行几乎相同的文本
   （`2. id: … distance=0.560` 与 `2. 编号：… 距离=0.560`），一度判定为 markdown 拆行。
   Grep 源码确认模块里根本没有「编号」「距离」字样，AppTest 里 `at.markdown` 也只有一条 `'1. id: TESTID-1　distance=0.500'`（异常 0）
   → 判明是 **Edge 浏览器译文扩展**对英文术语做双语叠加显示。仍预防性把该行从 `st.write` 改成 `st.text`（纯文本），源码留注释。

**本轮踩的坐标坑（写进经验库）**：

CU 坐标 x=1000 对应像素 1920，而浏览器窗口右缘只到约 1668 —— `cu.click(1000,350)` / `cu.scroll(1000,700,-8)`
全都落在窗口外的桌面上，页面根本没收到事件，表现为「滚动完全没反应」。
改用窗口内坐标（x 取 200~860、y 取 100~900）后滚动立刻生效（向上为正、向下为负，`8` 约 2/3 屏）。
另：`st.text_input` 必须回车或失焦才提交；`st.text_area` 必须 Ctrl+Enter 才提交。

**遗留**：

- 知识库页「零工具」是正确形态（不是遗漏）：该能力一直由主循环 / `skill_extractor` / research / tutor 直接调服务层，
  所以 `plugin_export = {}` 能通过 `registry.validate_tools` 的空 dict 校验。
- `sandbox/knowledge_pending.json` 仍有 **19 条待仲裁**（全为互补，含「我无法帮助…」这类低质量条目），等人工采用或丢弃。
- `kp_hits` 只存 session_state、不落盘，页面切走再回来检索结果会丢，需要重新检索。

## 十三、运行模式页迁入插件 + 工具链零面板迁出（2026-11-09）

第九、第十个插件，一次做完两个小体量的：`mode_plugin`（带面板）与 `toolchain_plugin`（零面板）。

**这轮的核心判断不是「先做小的」，而是「没有现成整页的功能不该新造面板」。**

### 13.1 选型与两者形态

| 功能 | 原位置 | 工具数 | 有无现成整页 | 做法 |
|---|---|---|---|---|
| 运行模式 | `tools/mode_tool.py` + `dashboard.py` 页 11 的「运行模式」tab | 2 | **有**（页 11 t111，12 行） | 2 工具 + 整页面板，tab 整块搬走 |
| 工具链宏 | `tools/toolchain_tool.py` | 2 | **无**（页 11 三个 tab 与此无关） | **只迁 2 工具，零面板** |

`toolchain_plugin` 的 manifest 里**不写 `panel` 字段** —— `registry.list_panels()` 只取 `manifest["panel"]` 为 dict 的项，
所以它不会在侧边栏出现。这是 `calc_plugin` 早就验证过的合法形态，不是遗漏。
**判据：面板上有没有可搬的整页。没有就不造 —— 造了就是新增功能，超出「把已有能力外置」的范围。**

### 13.2 一处必须先核实的「按钮是不是假的」

`mode_strategy` 是 `core/mode/strategy.py` 里的**进程内单例**，`_manual_mode` 与 `_samples` 都不落盘、不跨进程共享。
所以「面板上点『设为 steady』到底改到了谁」取决于**面板与主循环是否同进程**。

核实结果：`dashboard.py:21` 直接 `from main import react_loop, start_background_services, ALL_TOOLS` ——
**面板与主循环在同一进程**，`mode_strategy` 是同一个对象，按钮真实有效。
插件里同样 `from core.mode import mode_strategy`，仍在该进程内，行为不变。

这条结论已写进 `mode_plugin/module.py` 文件头，并注明「若以后把主循环拆成独立服务进程，这组按钮会静默失效，
届时要把 `_manual_mode` 改成落盘状态，不能只改这一页」。

### 13.3 一个必须遵守的写法：streamlit 只能在 panel_render 内部导入

`mode_plugin/module.py` 首版把 `import streamlit as st` 写在模块顶部，随后改掉了。
现有插件（`debate_plugin` 等）一律在 `panel_render()` 函数体内 `import streamlit as st`：

- 插件模块由 `_FilePlugin.load()` 在**模型侧（`main.py` CLI 路径）也会加载**；
- 顶部 import streamlit 会让模型侧加载插件时连带拖入 streamlit 及其依赖；
- 只在渲染面板时导入，则纯工具插件/工具路径完全不碰 UI 依赖。

### 13.4 落地内容

| 文件 | 动作 |
|---|---|
| `plugins/installed/mode_plugin/manifest.json` | 新建。`panel={"label":"📊 运行模式","order":100}`；`config.section="mode"` |
| `plugins/installed/mode_plugin/module.py` | 新建。2 工具（函数体逐字照搬）+ `panel_render()`；`import streamlit as st` 在函数内 |
| `plugins/installed/toolchain_plugin/manifest.json` | 新建。**无 `panel` 字段**；`config.section="toolchain"` |
| `plugins/installed/toolchain_plugin/module.py` | 新建。2 工具（函数体逐字照搬），**无 `panel_render`** |
| `main.py` | 删 2 组 import（`toolchain_tool` / `mode_tool`）与 `_PLUGIN_SOURCES` 2 项（现 8 项 / 20 工具） |
| `dashboard.py` | 页 11 `st.tabs` 3 → 2，删「运行模式」tab 整块（12 行）；删 `from core.mode import mode_strategy, MODES` |
| `tools/toolchain_tool.py`、`tools/mode_tool.py` | 只改文件头加迁移注释，函数体保留（可回滚） |
| `dashboard_apptest.py` | **未改** —— 它用「内置 7 项 + `registry.list_panels()`」自动拼目标页数，插件多一个页自动跟随 |
| `core/mode/strategy.py`、`core/toolchain.py` | **一行未动** |

### 13.5 验收表

| 项 | 结果 |
|---|---|
| `py_compile`（6 个改动文件） | 全 ok |
| 注册表 | `list_panels()` **8 条**：30/40/50/60/70/80/90/**100 📊 运行模式**；`all_tools()` **23**；内置 20 + 23 = **43**（总数不变） |
| 侧边栏 | **15 页**，末项「📊 运行模式」 |
| 面板首屏 | 标题 + caption + 「当前生效模式 steady —— 执行型…」+ 样本数行 + 三个按钮 + 关键词预判规则 |
| 设为 inquiry | 绿框「已切换为 inquiry（求证型）」，当前模式变 inquiry，手动指定显示 inquiry |
| 设为 steady | 绿框「已切换为 steady（执行型）」，当前模式变 steady，手动指定显示 steady |
| 清除手动指定 | 绿框「已清除手动指定，回到自动判定」，手动指定回到「无（按规则自动判定）」 |
| 页 11 | `st.tabs` 只剩「工具耗时」「模型闲聊」两个，原「运行模式」tab 已消失 |
| `dashboard_apptest.py` | **15/15 页通过**（目标 15 / 实际 15） |
| `python -m unittest test_suite` | **159/159 OK**（39.2s） |
| 验收后状态 | 手动指定已清除回「无」，与验收前一致 |

### 13.6 遗留

- 插件化进度：**10 / N**。仍内置的是 `workspace`(11)、`tutor`(2)、`desktop`(2)、`vision` / `learn` / `ask` / `obsidian` / `multi_model`（各 1）。
- `workspace`(11) 仍单独留一轮：需要新造面板（页 2 是视觉工作台，不是 workspace 工具的操作面），且直接碰桌面操控与视觉 ACL。
- `manifest.json` 的 `config` 段仍是空声明（无读取逻辑，`config.toml` 无对应段）。
- 手动指定模式不落盘：重启面板后回到「无」。这是 `core/mode/strategy.py` 的既有行为，本轮未改。
- 全部改动仍在工作区，未 commit。
