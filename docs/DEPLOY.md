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

> 注：下面的 `onlogon` 版本需要**用户登录**才会跑。无人值守（停在登录界面）的机器请用 `AtStartup` + `S4U`，命令见第六节 6.3。

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

---

## 六、实测记录：2026-11-14 旧笔记本（Haier，i7-4710MQ / 8G / 2G 显存 / Win10 未激活）

这是"常驻服务器"的第一台实测目标。从裸机到开机自活，以下是**实际**走过的路径与结果，不是设计稿。

### 6.1 硬件与系统事实

| 项 | 实测值 |
|---|---|
| CPU / 内存 | i7-4710MQ / 8,114 MB（运行中空闲约 4.2 GB） |
| 显卡 | NVIDIA GeForce 940M 2048 MB（驱动 22.21.13.8178）+ Intel HD 4600 |
| 磁盘 | C 盘剩余 65.6 GB（清理安装包后） |
| 系统 | Windows 10（未激活），PowerShell **5.1.19041.6456** |
| Python | 3.11.9（官方 amd64 安装包，PrependPath=1） |
| Ollama | 0.40.16 |
| 模型 | qwen2.5:3b（下载 1.9 GB，加载占用 2.2 GB） |
| 实测速度 | **11.1 tok/s**（eval_count=10 / 9.04 s，100% CPU） |

**结论：8G 内存 + 2G 显存的 2014 年笔记本，跑 3B 模型是"能用"的下限，不是"好用"。**

### 6.2 这次踩到的四个坑（都已修进仓库）

1. **`init_env.ps1` 无 BOM** → PS 5.1 按 GBK 解码中文注释，报 `字符串缺少终止符` / `RedirectionNotSupported`，脚本必崩。**已修：`ca77b40` 补 UTF-8 BOM。**（`launch_panel.ps1` 本来就有 BOM，所以它没事）
2. **`config.example.toml` 写着"路径留空即默认"，代码没实现** → `resolve_path("")` 原样返回空串 → `DB_PATH=".queue.db"` → `os.path.dirname` 得 `''` → `os.makedirs('')` 抛 `WinError 3`，面板起不来。**已修：`3a76c01` 空值回退默认目录 + `queue.py` 的 `makedirs` 加空目录保护。**
3. **ChromaDB 是同一个空路径根因** → `[kb] persist_path=""` 时报 `unable to open database file (code 14)`。修法同上。
4. **目标机只有 3389 开放，没法远程接管** → 加开 WinRM：`Enable-PSRemoting -Force -SkipNetworkProfileCheck` + `LocalAccountTokenFilterPolicy=1`，之后可全程远程（`ADMIN=True`）。

### 6.3 常驻的正确做法（不需要登录）

启动文件夹的 `Ollama.lnk`、`onlogon` 任务都要求**用户登录**。无人值守服务器应该用 **`AtStartup` + `S4U`**：

```powershell
$py  = '<项目路径>\.venv\Scripts\python.exe'
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
         -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$pr  = New-ScheduledTaskPrincipal -UserId 'zyc' -LogonType S4U -RunLevel Highest
$trg = New-ScheduledTaskTrigger -AtStartup

Register-ScheduledTask -TaskName 'agent_ollama' `
  -Action (New-ScheduledTaskAction -Execute '<Ollama安装路径>\ollama.exe' -Argument 'serve') `
  -Trigger $trg -Principal $pr -Settings $set -Force

Register-ScheduledTask -TaskName 'agent_panel' `
  -Action (New-ScheduledTaskAction -Execute $py -Argument '-m streamlit run dashboard.py --server.headless true --server.port 8501 --browser.gatherUsageStats false' -WorkingDirectory '<项目路径>') `
  -Trigger $trg -Principal $pr -Settings $set -Force
```

- `RestartCount 3 / RestartInterval 1min`：进程崩了自动拉回。
- **删掉启动文件夹里的 `Ollama.lnk`**，否则登录后会再起一个 app 与 serve 抢 11434。
- 模型常驻：`[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE","30m","Machine")`，重启 Ollama 生效；`ollama ps` 的 `UNTIL` 列会显示剩余常驻时间（实测 `29 minutes from now`）。

### 6.4 验证清单实测结果

- [x] `ollama list` / `/api/tags`：qwen2.5:3b 在
- [x] 面板 8501：本机 `HTTP 200`；**跨机 `http://<主机IP>:8501` 也返回 `200`**（主力机实测）
- [x] 关键导入链三项全 OK：`tools.desktop_control_tool`、`core.chroma_kb.get_chroma_kb()`、`services.knowledge_service`
- [x] 真实推理：`react_loop("smoke-remote-01","1+1等于几？")` → 返回 `1+1等于2。`，沙盘节点创建成功
- [x] 两个计划任务 `Running`，`LastTaskResult=267009`（= 正在运行，正常）
- [x] 清理安装包回收 **1,798 MB**（OllamaSetup 1,772 + python-setup 25 + agent.zip 1）
- [x] **重启验证自启（2026-11-14 17:31 实测通过）**：重启后 **16 秒**，两个 S4U 任务已 `Running`（`LastTaskResult=267009`），`ollama`（pid 4948）与 `streamlit`（pid 6840）均于 17:31:25 启动，8501 在 17:31:52 已可 TCP 连接；WinRM 慢一些，17:33:34 才起。**全程无人登录，面板与推理都自己活了。**

### 6.5 性能观察（重要）

单次 `react_loop` 冷启动 **81.8 秒**，其中约 80 秒是 `all-MiniLM-L6-v2` embedding 模型初始化（CPU 上 torch 加载），模型推理只占很小一部分。

- 面板进程常驻后，embedding 只初始化一次；
- 但每次独立进程跑任务都要再付一次——后续可考虑换 ONNX 后端，或把 `KnowledgeService` 做成进程级单例预热。
- `qwen2.5:3b` 加载日志 `6.2s/8.0s（显存不足需换入）`：2.2 GB 模型 > 2 GB 显存，全 CPU 是预期结果，**不要尝试强行塞显存**。

### 6.6 本次用到的远程部署手法（无 git / GitHub 不可达时）

目标机访问不了 GitHub（443 不通），也没有 git。实际走的是：

```powershell
# 主力机
$sess = New-PSSession -ComputerName <目标IP> -Credential $cred
Copy-Item -ToSession $sess -Path '<主力机文件>' -Destination '<目标机路径>' -Force
Invoke-Command -Session $sess -ScriptBlock { ... }
```

只推了 `core/config_loader.py` 和 `core/desktop/queue.py` 两个修复文件，就解决了空路径问题——比等 GitHub 通更直接。

### 6.7 软件精简：服务器上该卸什么、什么绝对不能碰

这台旧笔记本原本装了 20 个传统程序 + 一堆商店应用。做服务器只需要其中很小一部分。

**已卸（实测有效，磁盘 65.6 → 67.49 GB）：**

| 软件 | 为什么卸 | 卸法 |
|---|---|---|
| **WeGame**（腾讯游戏平台，548.8 MB） | 服务器不打游戏；它还会挂反作弊模块 | 注册表的卸载器路径是假的，实际卸载器在 `wegame_update\uninst.exe`，且 `/S` 不被支持 → 直接删目录 + 删 `HKLM\...\WOW6432Node\...\Uninstall\WeGame` 键。**确认过没有服务、没有快捷方式，删了不影响系统** |
| **Microsoft OneDrive** | 云同步盘，服务器不需要，还常驻后台 | `OneDriveSetup.exe /uninstall`，静默干净 |
| **Copilot** | 微软的 AI 助手，这台机器跑的是我们自己的 agent | `copilot_setup.exe --uninstall --mscopilot --channel=stable --system-level`。注册项会立刻消失，但**程序目录（471 MB）删不掉**（`d3dcompiler_47.dll` 拒绝访问），先 `Rename-Item` 改成 `Copilot.disabled_<日期>` 让它失效，重启后再删 |
| **29 个商店应用** | 见下 | `Remove-AppxPackage` |

商店应用卸载清单（实测 26 个成功，2 个系统级卸不掉属正常）：

```powershell
$names = @(
  'Microsoft.BingWeather','Microsoft.MicrosoftSolitaireCollection','Microsoft.Microsoft3DViewer',
  'Microsoft.MixedReality.Portal','Microsoft.SkypeApp','Microsoft.ZuneMusic','Microsoft.ZuneVideo',
  'Microsoft.WindowsMaps','Microsoft.YourPhone','Microsoft.WindowsFeedbackHub','Microsoft.GetHelp',
  'Microsoft.Getstarted','Microsoft.OutlookForWindows','microsoft.windowscommunicationsapps',
  'Microsoft.XboxApp','Microsoft.XboxGamingOverlay','Microsoft.XboxGameOverlay',
  'Microsoft.XboxSpeechToTextOverlay','Microsoft.WindowsAlarms','Microsoft.Wallet',
  'Microsoft.MicrosoftPCManager','MicrosoftWindows.CrossDevice','Microsoft.People',
  'Microsoft.Office.OneNote','Microsoft.MicrosoftStickyNotes'
)
foreach ($n in $names) {
  $p = Get-AppxPackage -Name $n -ErrorAction SilentlyContinue
  if ($p) { try { $p | Remove-AppxPackage -ErrorAction Stop; "OK $n" } catch { "FAIL $n" } }
}
```

**卸不掉但无害的两个系统级应用**：`Microsoft.Windows.ParentalControls`、`Microsoft.Windows.NarratorQuickStart` —— 在 `C:\Windows\SystemApps` 下，报 `0x80073CFA`，属 Windows 自身组件，留着不影响。

**绝对不能卸（会直接坏掉）：**

| 组件 | 为什么 |
|---|---|
| **Visual C++ 2010/2012/2013/2015-2022 Redistributable**（六个） | python、torch、onnxruntime、chromadb 全依赖，卸了项目起不来 |
| **NVIDIA 图形驱动程序** | 卸了桌面会回落到 800×600 |
| **ELAN Touchpad** | 卸了触摸板失效 |
| **Microsoft Edge / Update Health Tools / KB 更新** | 系统组件 |
| **Microsoft.VCLibs / .NET.Native / UI.Xaml / WindowsAppRuntime**（Appx） | 商店应用与部分工具的运行库 |
| **Microsoft.WindowsStore / DesktopAppInstaller / Winget.Source** | 卸了以后装软件很麻烦 |
| **Shell 组件**（ShellExperienceHost / StartMenuExperienceHost / Search / LockApp 等） | 卸了桌面直接崩 |

**保留但可以再议的**：`Vulkan Run Time Libraries`（某游戏带来的，约 10 MB，卸载器是 GUI，收益太小不值得动）、相机、照片、画图、截图、计算器、录音机（都是系统自带的小工具，服务器上偶尔还能用）。
