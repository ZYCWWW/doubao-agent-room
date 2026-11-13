# 部署指南：换一台机器怎么把 Agent Room 跑起来

> 这份文档解决一件事：**这个项目现在绑在一台机器上，怎么把它搬到另一台机器上，并且开机就活。**
> 配置怎么改见 `config/config.example.toml` 注释；面板怎么用见 `docs/PANEL_HELP.md`。

---

## 一、先看结论：哪台机器能跑什么

| 机器 | 能不能跑主项目 | 实际能跑什么 |
|---|---|---|
| Windows 桌面/笔记本（x86_64，8G+） | **能** | 完整项目：面板 + ReAct + 群聊 + 视觉工作台 |
| Linux x86_64（4G+，有桌面可选） | **能**（无视觉） | 面板 + ReAct + 群聊；视觉/键鼠类不可用 |
| ARM 小盒子（S905L3A/L3B 一类，2G） | **不能** | 只能当外围节点：备份拉取、探活、转发 |

**为什么 2G 的 ARM 盒子跑不了：** `requirements.txt` 里的 `sentence-transformers` 会拖入 torch（数 GB），`chromadb` 还要 onnxruntime。2G 内存、ARM 架构，装得上也跑不动——这不是配置问题，是硬件门槛。

所以“永不关机”的正确结构是：

```
主机（笔记本 / 常开的 PC）  ── 跑完整的项目，负责说话、记忆、面板
       │
       └─ 外围节点（盒子 / 树莓派一类）── 只做三件事：定时把主机的备份拉过来、
                                          定时探主机活不活、不通就发告警
```

盒子不是“第二台推理机”，是“主机倒下时还能叫人的那个”。

---

## 二、Windows 主机：完整安装

### 1. 前提

- Windows 10/11，x86_64，内存 8G 起（4G 可以跑，但 4B 模型会很紧）
- Python 3.11 以上（3.13 已验证）
- [Ollama](https://ollama.com/download) 已安装并能在 `http://localhost:11434` 访问
- Git（要拉仓库的话）

### 2. 拿到代码

```powershell
git clone <this-repo> agent-room
cd agent-room
```

或者整个目录拷过去（**不要拷 `.venv`**，虚拟环境跟机器绑定，换机器必须重建）。

### 3. 建环境装依赖

```powershell
powershell -ExecutionPolicy Bypass -File .\init_env.ps1
```

脚本会：建 `.venv` → 升级 pip → 按 `requirements.txt` 装依赖。首次会拉 torch，几 GB，慢是正常的。

### 4. 复制配置并修改

```powershell
copy config\config.example.toml config\config.toml
```

然后按这台机器改。**要改的只有三类：**

1. **模型名**：`[ollama] chat_model` / `[models] main` 填这台机器 `ollama list` 里实际有的
2. **路径**：`[library] path`（书库）、`[external_kb] path`（外部资料库）—— 留空就用项目目录内默认位置
3. **云端模型**：`[external_llm] enabled = true` 时才需要，密钥走环境变量 `DEEPSEEK_API_KEY` 或 `config/secrets.toml`，**永远不要写进 config.toml**

### 5. 拉模型

```powershell
ollama pull qwen3.5:4b          # 视觉 + 轻量推理，约 2.5G
ollama pull llama3.1:8b         # 群聊里的轻量角色
ollama pull deepseekR1Q7B:latest  # 主模型（如果要用）
```

**显存不够时的规则**：Ollama 会自动把放不下的层放回 CPU，不要手动设 `num_gpu`。2G 显存的机器跑 4B 基本全在 CPU，慢是预期内的。

### 6. 起面板

```powershell
.\启动面板.bat
```

或者直接：

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard.py --server.headless true
```

浏览器开 `http://localhost:8501`。

### 7. 常驻：让它在开机后自己活过来

面板和群聊都要有人拉起。用**计划任务**，不要靠手动。

**面板（开机即起，用户登录后）：**

```powershell
schtasks /create /tn "agent_project_panel" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File <项目路径>\launch_panel.ps1" /sc onlogon /rl highest /f
```

**长期闲聊服务（开机即起）：**

```powershell
schtasks /create /tn "agent_project_long_chat" /tr "powershell -NoProfile -Command \"& '<项目路径>\.venv\Scripts\python.exe' -c 'import sys; sys.path.insert(0,r\"<项目路径>\"); from services.long_chat import long_chat_start; print(long_chat_start())'\"" /sc onlogon /rl highest /f
```

> 注意：必须走 `long_chat_start()`，**不能用 `start_service()`** —— 后者写的是调用者自己的 pid，进程退出后状态还显示“已启用”，会误判服务在跑。

**每日备份（23:30）：**

```powershell
schtasks /create /tn "agent_project_memory_backup" /tr "<项目路径>\.venv\Scripts\python.exe <项目路径>\tools\backup_memory.py" /sc daily /st 23:30 /rl highest /f
```

**看门狗（每 5 分钟）：**

```powershell
schtasks /create /tn "agent_project_watchdog" /tr "<项目路径>\.venv\Scripts\python.exe <项目路径>\tools\watchdog.py" /sc minute /mo 5 /rl highest /f
```

### 8. 验证清单

装完逐条过，全绿才算部署成功：

- [ ] `ollama list` 能看到配置里写的模型
- [ ] 面板 `http://localhost:8501` 能打开，7 个内置页签都在
- [ ] 面板里「地址管理器」显示的各条路径都指向这台机器真实存在的目录
- [ ] 群聊能出一句话（`sandbox/chat_space/chat.md` 末尾有新行）
- [ ] `tools/backup_memory.py` 手动跑一次，输出目录有 `manifest.json` 且 `failed` 为空
- [ ] 四个计划任务在 `schtasks /query` 里都是 Ready
- [ ] 重启一次机器，面板和群聊自己起来

---

## 三、Linux / ARM 外围节点：只装该装的那点

盒子刷了 Armbian 之后，**不要试图装完整项目**。它只做外围。

### 它要做的事

1. **定时拉备份**：从主机把 `记忆备份/最新日期` 同步过来（`rsync -a --delete`）
2. **定时探活**：检查主机的面板端口和 Ollama 端口通不通
3. **不通就告警**：写本地日志 + 发一条消息（邮件/webhook/你选的通道）

### 最小依赖

```bash
sudo apt update && sudo apt install -y rsync curl python3
```

**不要装**：torch、chromadb、streamlit、sentence-transformers。2G 内存装完就没得用了。

### 一个探活脚本的样子

```bash
#!/bin/bash
# /opt/agent-room-probe/probe.sh
HOST="192.168.0.199"
LOG="/var/log/agent-room-probe.log"
ts=$(date '+%Y-%m-%d %H:%M:%S')

if curl -sf --max-time 5 "http://$HOST:8501" >/dev/null; then
  panel="UP"
else
  panel="DOWN"
fi

if curl -sf --max-time 5 "http://$HOST:11434/api/tags" >/dev/null; then
  ollama="UP"
else
  ollama="DOWN"
fi

echo "$ts panel=$panel ollama=$ollama" >> "$LOG"

if [ "$panel" = "DOWN" ] || [ "$ollama" = "DOWN" ]; then
  # 这里接你的告警出口：curl webhook / mail / 等
  echo "$ts ALERT panel=$panel ollama=$ollama" >> "$LOG"
fi
```

### systemd timer（每 5 分钟）

```ini
# /etc/systemd/system/agent-probe.service
[Unit]
Description=Agent Room health probe

[Service]
Type=oneshot
ExecStart=/opt/agent-room-probe/probe.sh
```

```ini
# /etc/systemd/system/agent-probe.timer
[Unit]
Description=Run agent probe every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-probe.timer
```

### 拉备份的 timer

```bash
# /opt/agent-room-probe/pull-backup.sh
rsync -a --delete "//<主机>/记忆备份/$(date +%Y%m%d)/" "/mnt/backup/$(date +%Y%m%d)/"
```

> Windows 主机做 rsync 服务端比较麻烦。更省事的替代：主机上跑一个每日任务，用 `scp`/`robocopy` 反向推到盒子，或者干脆用 SMB 共享 + `mount.cifs`。

---

## 四、迁移数据（换机器时）

要带走的是**记忆**，不是代码（代码在 git 里）。

| 目录 | 内容 | 要不要带走 |
|---|---|---|
| `agent_memory_db/` | chromadb 向量记忆 | **要** |
| `docs/conversations/` | 每日对话纪要 + raw 原文 | **要** |
| `sandbox/chat_space/` | 群聊流水、状态、按日归档 | **要** |
| `config/config.toml`、`config/secrets.toml` | 本机配置与密钥 | 要（但不进 git） |
| `.venv/` | 虚拟环境 | **不要**，换机器重建 |
| `logs/` | 日志 | 可不带 |

带走方式：先在新机器上跑一次 `tools/backup_memory.py` 确认目录结构，再把旧机器的三块记忆目录覆盖进去。**不要直接拷整个项目目录**——`.venv` 会带过去，那套二进制跟旧机器绑定。

---

## 五、常见坑

1. **`start_service()` 写错 pid**：外部拉起服务必须用 `long_chat_start()`。
2. **日志文件被占用**：服务进程正在写 `long_chat.log` 时，普通读取会报“文件被另一个进程使用”；用共享读（PowerShell 的 `[IO.File]::Open($p,'Open','Read','ReadWrite')`）。
3. **控制台 emoji 报错**：Windows GBK 控制台遇到 `❌` 会抛 `UnicodeEncodeError`，已在 `core/logger.py` 统一成 UTF-8 处理。
4. **模型“已加载”不等于在显存里**：`/api/ps` 里 `size_vram` 远小于 `size` 时，一半层在 CPU，每次调用仍要换入，慢几十秒。
5. **ARM 装不了 torch**：别硬装，2G 内存的盒子本来就不该跑这个项目。
