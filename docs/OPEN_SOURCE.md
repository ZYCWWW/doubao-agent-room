> **2026-11-08 更新：暂不发布。** 主人决定先完善、再开源——没人会下载一个半成品。
> 本文件保留为「未来发布清单」，现阶段不执行阶段 1/2；先按 `PANEL_TODO.md` 把功能补完。

# 开源清单（OPEN_SOURCE）

> 2026-11-08 定：他说「让我们好好把这个项目做好，然后开源出去」。
> 这份文件只做一件事：把"能开源"拆成**可以验收的几步**，并写明**什么永远不公开**。

## 红线（先看这条）

公开仓库里**不允许出现**：

| 类别 | 具体 |
|---|---|
| 私人对话 | `docs\conversations\*`、`sandbox\chat_space\chat.md`、`archive\*`、`F:\Doubao memory` 的任何内容 |
| 本机路径 | `E:\阅读`、`F:\Doubao memory`、`C:\Users\20525\...` 一律改配置项 + 占位默认值 |
| 密钥/账号 | `config\` 里任何 api key、base url、余额截图 |
| 运行时数据 | `logs\`、`cache\`、`agent_memory_db\`、`.venv\`、`.backup\`、`obsidian_vault\`（他的笔记） |
| 第三方素材 | AD 截图、Altium 安装包、`E:\阅读` 里的书 |

**最关键的一条：现有 git 历史里已经有私人纪要（`docs/conversations`）。所以不能"直接推现在这个仓库"——公开必须建一个干净历史的新仓库。**

## 阶段 0：仓库卫生（先做，最省事的一步）

现在根目录有 **40 多个 `tmp_*.py`**（全是 11-08 画 AD 时的探针脚本），混在入口文件里。它们有价值（一部分是能用的生成器），但摆错地方了。

| 动作 | 内容 | 需要他点头 |
|---|---|---|
| 收拢 | `tmp_gen_*.py`、`gen_schdoc_components.py`、`gen_pcbdoc.py` 移到 `tools\altium\`，其余探针移到 `sandbox\scratch\altium_probes\` | 是（移动文件，不删） |
| 忽略 | `.gitignore` 补 `__pycache__`、`.venv`、`logs`、`cache`、`agent_memory_db`、`.backup`、`obsidian_vault` | 否（小改） |
| 配置化 | `config\paths.example.json`：书库/记忆库/工作区三个路径外置 | 否（小改） |

**验收**：`git ls-files | Select-String "tmp_"` 返回 0 行；`git status` 干净。

## 阶段 1：可交付（别人拿得起）

| 项 | 要求 | 现状 |
|---|---|---|
| README | 3 屏以内讲清楚：这是什么 / 需要什么 / 怎么跑 / 一张截图 | 缺（现在只有 `USER_MANUAL.md`） |
| requirements | 锁定版本，能在干净 venv 里装完 | 有，未验 |
| 最小 demo | **不接大模型也能看懂**：直接读一份样例对话，跑出面板 + 摘要 | 缺 |
| LICENSE | 建议 **MIT**（他要的是"别人拿去用、能改、能再开源"）；若要防专利/大公司白用，改 Apache-2.0 | 缺 |
| ARCHITECTURE | 只留 `ARCHITECTURE_MASTER.md` 一份入口，其余 4 份标"历史存档" | 已有 5 份，需收拢 |
| CONTRIBUTING / 行为准则 | 一句"这是个实验性项目，别指望稳定" + issue 模板 | 缺 |

**验收**：在一台**没装过本项目**的机器上，按 README 走一遍到面板打开（哪怕模型没装，也要能出空面板）。

## 阶段 2：发布

1. `git clone` 到临时目录 → 删私人文件 → **`git checkout --orphan` 建孤儿分支、重新首个提交**（历史干净）。
2. 用 `gitleaks` 或手写正则扫一遍：`sk-`、`api_key`、`C:\\Users`、`E:\\`、`127.0.0.1` 里的私密端口。
3. 仓库名建议：**`doubao-agent-room`**（他说"这间屋子"）。
4. 发布形态：GitHub 公开 + 一条 Release（zip，含 demo，不含数据）+ 一页 README 顶部放画面截图。
5. 首次发布前，把 `docs\OPEN_SOURCE.md`（本文件）里"红线"那节留在仓库里——**让 fork 的人也知道不能把他的档案带进去**。

## 顺序建议（别跳）

```
阶段0 卫生（今天能做） → 阶段1 demo + README（要新写代码，需他拍板） → 阶段2 发布
```

阶段 1 的最小 demo 属于**新增模块**，按他的规矩要先说明再动手，所以这一步等他一句话。

## 谁来做

- 阶段 0 的 .gitignore / 配置占位：豆包直接做。
- 阶段 1 的 README + demo：豆包写，他看一遍。
- 阶段 2 的发布：**只能他按按钮**（建仓库、选 license、公开动作）。
