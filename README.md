# Agent Room · 一个跑在自己电脑上的 AI 驾驶舱

一个本地优先的 AI Agent 实验项目：ReAct 主循环 + 多模型协作 + 可视化面板，
所有数据留在自己机器上，不依赖任何云服务（除可选的 OpenAI 兼容接口）。

> 这不是一个"开箱即用的产品"，更像一间可以自己改装的屋子。
> 代码以模块为单位放着，想拆哪块、换哪块都行。

---

## 它是什么

- **ReAct 主循环**：任务拆解 → 调工具 → 观察结果 → 再规划，带审批队列（危险操作要人点头）
- **多模型协作**：本地 Ollama 跑若干个模型，按角色分派（推理 / 快速 / 视觉）
- **记忆分层**：短期上下文 → 情景记忆（四分区打分下沉）→ 长期知识库（三个集合）
- **可视化面板**：一个 Streamlit 单页，内置 7 页，插件各自带自己的页签
- **视觉工作台**：把一个桌面软件"嵌进舞台"，让模型只在那一块窗口里看图与操作
- **插件系统**：`plugins/installed/` 放本地插件，热加载，面板自动出现新页签

## 你需要什么

| 依赖 | 说明 |
|---|---|
| Python 3.11+ | 项目在 3.13 上开发验证 |
| [Ollama](https://ollama.com) | 本地模型服务，默认 `http://localhost:11434` |
| 至少一个模型 | 例如 `ollama pull llama3.1:8b` |
| Windows（可选） | 视觉工作台与桌面操作依赖 Win32；纯 ReAct 与面板在 Linux/macOS 也能跑 |

## 怎么跑

```bash
git clone <this-repo> agent-room
cd agent-room

python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/mac: source .venv/bin/activate
pip install -r requirements.txt

# 依赖已锁精确版本，保证装出来的环境一致。
# 首次安装会拉入 torch（经 sentence-transformers），体积较大，请留足时间和磁盘。
# 需要连传递依赖一起复现时：pip install -r requirements.lock.txt

# 复制示例配置，按自己机器改（不改也能跑，全用默认值）
copy config\config.example.toml config\config.toml      # Windows
cp config/config.example.toml config/config.toml        # Linux/macOS

streamlit run dashboard.py --server.headless true
```

浏览器打开 <http://localhost:8501>。

### 只想先看看面板

不装 Ollama、不填任何配置，直接跑 `streamlit run dashboard.py` 也能打开完整面板：
所有内置页签和插件页签都会正常渲染，只是点进具体功能时会提示"未连接模型"。

面板本身不依赖模型在线——先看结构，再决定要不要接本地模型。

> **Linux / macOS 用户注意**：面板本身可以在无图形界面的服务器上运行，但"视觉工作台"和桌面键鼠类工具依赖图形界面和 Win32 接口。没有显示环境或不是 Windows 时这些功能不可用（会给出明确提示），其余功能不受影响。需要完整桌面能力请在 Windows 桌面环境下使用。

命令行跑一次 ReAct：

```bash
python main.py
```

跑测试（159 个用例）：

```bash
python -m unittest test_suite
```

## 配置与密钥

- `config/config.example.toml` 是模板，复制成 `config/config.toml` 后改。
- 所有路径留空即用项目目录内的默认位置（`sandbox/`、`agent_memory_db/`、`logs/`、`books/`）。
- **密钥永远不写进配置文件**：优先环境变量 `DEEPSEEK_API_KEY`，其次 `config/secrets.toml`（已 gitignore）。
- 没有配密钥时，云端参与者自动不启用，本地部分照常工作。

## 目录

```
core/          主循环、模型路由、记忆、知识库、插件注册、视觉、研究流水线
services/      长期闲聊、书库、聊天空间等常驻服务
tools/         暴露给模型的工具函数
plugins/       已安装插件 + 市场目录
dashboard.py   Streamlit 面板（内置 7 页 + 插件页）
main.py        命令行 ReAct 入口
config/        配置模板与运行时清单
docs/          架构与面板文档
```

## 数据与隐私

这个项目**默认不收集任何数据**，所有内容都在你自己机器上：

- 会话沙盘 `sandbox/`、记忆库 `agent_memory_db/`、日志 `logs/` 全部在 `.gitignore` 里
- 本仓库不含任何作者的个人对话、笔记或本机路径
- 想要备份，自己打包目录即可；不想留，删掉即可

## 记忆系统与技术日志

- 记忆存在哪里、四分区怎么划分、为什么内容不进仓库：见 [`docs/MEMORY_SYSTEM.md`](docs/MEMORY_SYSTEM.md)。
- 这个项目从第一天到现在怎么长起来的，整理成了**脱敏后的技术日志**：[`docs/tech-log/`](docs/tech-log/)。里面只有过程、判断和踩过的坑，没有使用者本人的任何信息。

## 许可

MIT，见 [LICENSE](LICENSE)。

## 更多文档

- 架构总览：[`docs/ARCHITECTURE_MASTER.md`](docs/ARCHITECTURE_MASTER.md)
- 面板逐页说明：[`docs/PANEL_HELP.md`](docs/PANEL_HELP.md)
- 使用手册（含视觉工作台细节）：[`USER_MANUAL.md`](USER_MANUAL.md)
