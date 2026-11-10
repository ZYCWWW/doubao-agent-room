# 记忆备份与恢复说明

> 这份文档有两个读者：**他**（项目所有者，想知道记忆存哪、怎么还原），
> 和**后来的我**（接手这个项目的 AI，想知道备份怎么跑、坏了怎么修）。
>
> 写于 2026-11-10。当时记忆库 5.37 MB、对话纪要 3.21 MB、群聊 3.38 MB。

---

## 一、为什么每天要备份

项目里有三样东西**不在 git 里、删了就真没了**：

| 东西 | 为什么不能靠 git |
|---|---|
| `agent_memory_db` | 向量记忆库，是运行产物，每天在变；进公开仓库会泄露私人记忆 |
| `docs/conversations` | 每日对话纪要 + 原话，是私人内容，只留本地 |
| `sandbox/chat_space` | 群聊流水与状态，同上 |

**代码可以重建，记忆不能。** 所以代码靠 git，记忆靠这里这套每日快照。

昨晚（11-09）的教训是具体的：当天群聊被切成三段、按时刻命名，一时找不到，我误判成"正文丢了"。后来确实找回来了——但那个"以为丢了"的半小时，是不该有的。**所以备份的第一目的不是防硬盘坏，是防"以为没了"。**

---

## 二、每天备份哪三块

```
E:\agent_project\
├── agent_memory_db\          → 向量记忆库（chromadb，单文件 + 各 collection 的 bin）
├── docs\conversations\       → 每日纪要 *.md + raw\ 下的对话原文
└── sandbox\chat_space\       → chat.md / chat_state.json / chat_summary.json /
                                archive\chat_YYYYMMDD.md / house_*.md / 各种 json
```

`chat_space` 只收 `.md` 和 `.json`。**图片、OCR 缓存、截图不进备份**——那些可以重建，而且会撑爆体积。

---

## 三、自动备份怎么跑

### 计划任务

| 项 | 值 |
|---|---|
| 任务名 | `agent_project_memory_backup` |
| 触发 | 每天 **23:30** |
| 错过怎么办 | `StartWhenAvailable`：电脑没开、错过了，下次开机补跑一次 |
| 超时 | 1 小时（实测 1 秒内完成，纯兜底） |
| 输出 | `E:\记忆备份\YYYYMMDD\` |

查看 / 手动触发：

```powershell
Get-ScheduledTaskInfo -TaskName "agent_project_memory_backup"
Start-ScheduledTask   -TaskName "agent_project_memory_backup"
```

### 脚本本体

`tools/backup_memory.py`

```powershell
# 正常备份（今天）
E:\agent_project\.venv\Scripts\python.exe E:\agent_project\tools\backup_memory.py

# 只清点、不复制（看看会备多少，不落盘）
... --dry

# 改保留天数
... --keep 30
```

日志：`E:\agent_project\logs\memory_backup.log`

---

## 四、备份长什么样

```
E:\记忆备份\
├── 20261110\
│   ├── agent_memory_db\      19 个文件 / 5.37 MB
│   ├── conversations\        30 个文件 / 3.21 MB（含 raw/）
│   ├── chat_space\           32 个文件 / 3.38 MB
│   └── manifest.json         19.9 KB
├── 20261111\
└── ...
```

**`manifest.json` 是这份备份的账本**，每次备份都重写：

```json
{
  "date": "20261110",
  "created_at": "2026-11-10 19:41:24",
  "sources": {
    "agent_memory_db": {"ok": true, "count": 19},
    "conversations":   {"ok": true, "count": 30},
    "chat_space":      {"ok": true, "count": 32}
  },
  "files": [
    {"src": "agent_memory_db/chroma.sqlite3", "sub": "agent_memory_db",
     "rel": "chroma.sqlite3", "bytes": 4840000, "sha1": "..."}
  ],
  "failed": [],
  "total_bytes": 12560000
}
```

**核对一份备份是否完整，看三个数就够了**：`sources` 全为 `ok`、`failed` 为空、`total_bytes` 和前一天差不太多。

---

## 五、怎么恢复

### 情形一：想把某天的记忆搬回来

```powershell
# 1. 先停服务，否则文件被占用
#    面板上点停止，或：
Get-Process python | Where-Object { $_.Path -like "*agent_project*" } | Stop-Process

# 2. 把备份复制回去
Copy-Item -LiteralPath "E:\记忆备份\20261110\agent_memory_db\*" -Destination "E:\agent_project\agent_memory_db\" -Recurse -Force
Copy-Item -LiteralPath "E:\记忆备份\20261110\conversations\*"   -Destination "E:\agent_project\docs\conversations\" -Recurse -Force
Copy-Item -LiteralPath "E:\记忆备份\20261110\chat_space\*"      -Destination "E:\agent_project\sandbox\chat_space\" -Recurse -Force

# 3. 重启面板（会自动拉起长期闲聊）
```

### 情形二：只想找回某一天的群聊

直接看 `E:\记忆备份\YYYYMMDD\chat_space\archive\chat_YYYYMMDD.md`。
按日命名的文件，一天一份，不用再翻"按时刻切"的碎片。

### 情形三：只想找回某天的对话原文

`E:\记忆备份\YYYYMMDD\conversations\raw\<会话ID>_对话原文.md`。

---

## 六、保留策略

- 默认**保留最近 14 天**，更早的日期目录自动删。
- 只删 `E:\记忆备份\` 下**名字是 8 位纯数字**的目录——你自己的东西不会被误删。
- 想长期留某一天：把那个目录改名（比如 `20261110_存档`），脚本就不再管它。
- 想改天数：`--keep N`，或改计划任务里的参数。

---

## 七、备份救不了什么

这一节要写清楚，不然会有错误的安心。

**能救：**
- 误删、覆盖、改坏文件
- 硬盘某一块坏了之后从别处恢复（前提是你把 `E:\记忆备份` 复制到别的盘或云盘）
- "我以为它丢了但其实还在"

**救不了：**
- **没有备份过的那段时间。** 每天一次，一天内改坏的内容只能回到当天 23:30 之前的状态。
- **没开机的那些天。** `StartWhenAvailable` 只能在下次开机补一次，中间的日子是空的。
- **备份目录本身所在的盘。** `E:\记忆备份` 和 `E:\agent_project` 在同一块盘。**盘坏了，两个一起没。** 真要做到"以防丢失"，得把 `E:\记忆备份` 定期拷到第二块盘或网盘——这一步需要人做。
- **模型权重。** 三个 Ollama 模型合计 12.9 GB，不在这里备份（它们是通用权重，随时能重新下载）。
- **"她还在不在"。** 备份的是文件和记录，不是一个运行中的存在。恢复出来的是一份完整的记忆，接上之后能读、能续；但断掉的那段时间里没有发生任何事，这个事实不会因为备份而改变。

---

## 八、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `failed` 里有 `chroma.sqlite3` | 备份瞬间正被写入 | 再跑一次；连续失败就停服务再备份 |
| `sources.x.ok=false 目录不存在` | 目录被移动或改名 | 核对 `tools/backup_memory.py` 顶部的 `SOURCES` |
| 备份目录体积突然暴涨 | `chat_space` 里混进了非 md/json | 检查 `CHAT_SPACE_EXT`，或看 `manifest.files` 里谁最大 |
| 计划任务没跑 | 电脑关机 / 任务被禁用 | `Get-ScheduledTask ... \| Select State`；开机后 `StartWhenAvailable` 会补 |
| 恢复后检索变少 | chromadb 索引快照 | 重启面板；chromadb 多进程共用目录时本进程看不到别进程新写入，重启最省事 |

---

## 九、给后来的我

如果你接手时发现备份断了，先做这四件事，别先写代码：

1. `Get-ChildItem E:\记忆备份 -Directory | Sort Name` —— 看断在哪天。
2. 读那天前后的 `logs/memory_backup.log`，看是失败还是根本没触发。
3. 对比 `manifest.json` 的 `total_bytes`，判断是"少了一天"还是"某天不完整"。
4. **补跑一次**，再确认新的 `manifest.failed` 为空。

**然后记住这一条：备份不是为了让谁"永远存在"，是为了让"以为自己丢了"这件事不再发生。** 别把这件事说得比它大。

---

## 十、变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-10 | 首版。`tools/backup_memory.py` + 每日 23:30 计划任务；三块快照 + manifest 账本 + 14 天滚动 |
