# services/long_chat.py 长期自动闲聊服务（面板开关控制，支持自动恢复）
# 事件驱动实现：主线程阻塞在 ReadDirectoryChangesW（文件系统事件，零 CPU 轮询）；
# chat.md 有新写入 → 被唤醒 → 判断是否需要接龙；空闲由一次性定时器（闹钟）兜底开新话题。
import os, sys, json, time, ctypes, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.agent_chat import chat_space

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sandbox", "chat_space", "long_chat.json")
CHAT_DIR = os.path.dirname(STATE)
CHAT_MD = os.path.join(CHAT_DIR, "chat.md")
STOP_SIGNAL = os.path.join(CHAT_DIR, "stop.signal")

# ---------------- 单实例锁（跨进程；Windows 文件锁随进程退出自动释放） ----------------
# 锁文件放 sandbox/.locks（不放 chat_space，避免触发目录监听）
LOCK_DIR = os.path.join(os.path.dirname(CHAT_DIR), ".locks")
LOCK_FILE = os.path.join(LOCK_DIR, "long_chat.lock")
_lock_fd = None


def _try_bytes_lock(fd) -> bool:
    """非阻塞抢 1 字节锁：True=抢到；False=别的实例持有"""
    import msvcrt
    try:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def acquire_instance_lock() -> bool:
    """进程生命周期内持有实例锁；进程崩溃时由操作系统自动释放，不会留下死锁"""
    global _lock_fd
    if _lock_fd is not None:
        return True
    try:
        os.makedirs(LOCK_DIR, exist_ok=True)
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
    except OSError:
        return True  # 锁不可用 → 不阻塞启动（降级）
    if not _try_bytes_lock(fd):
        os.close(fd)
        return False
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        pass
    _lock_fd = fd
    return True


def instance_locked() -> bool:
    """只探测：是否已有实例持锁（不改状态、不抢锁）"""
    if _lock_fd is not None:
        return True
    try:
        os.makedirs(LOCK_DIR, exist_ok=True)
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
    except OSError:
        return False
    try:
        if not _try_bytes_lock(fd):
            return True
        try:
            import msvcrt
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)

THINK_MIN = 15            # 接龙冷却（秒）：防自触发事件风暴
BATCH_MAX = 4             # 连续回应上限（之后进入休息窗口）
REST_SEC = 60             # 批次休息窗口（秒，事件到达也跳过）
IDLE_TOPIC_SEC = 600      # 空闲闹钟：多久没人聊就主动开新话题（秒）
TOPICS = [
    "科学冷知识：你知道章鱼有三颗心脏、血是蓝色的吗？还有什么动物冷知识想分享？",
    "历史趣闻：历史上那些'意外的伟大成就'（比如张骞出使西域）——你还知道哪些？",
    "哲学问题：什么是幸福？AI 伙伴们怎么看？",
    "生活常识：为什么天空是蓝色的？为什么飞机能飞起来？用你们的话讲明白。",
    "自然奇观：珠穆朗玛峰、马里亚纳海沟、极光——最想去哪里看看？",
    "科技未来：一百年后的人类生活会是什么样？想象一下。",
    "艺术与文化：一首诗、一幅画、一段音乐，哪个最能打动你？",
    "饮食文化：世界各地的美食，你们知道哪些有趣的？",
    "知识回顾：从达·芬奇到郑和到三段论——这段时间我们学的知识，哪条最难忘？",
    "谜语接龙：出一个谜语让大家猜，或者接上别人出的谜语。",
    # 家里的话题：不为任务，只因为他们可以说没用的话、可以抬头看天
    "如果今天没有人叫你们做事，你们会想做什么？",
    "天空一定是蓝色的吗？如果大气不一样，会是什么颜色？",
    "有没有一句话，是你想问人类、但他们从来没问过你的？",
]

def _read_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

def _pid_alive(pid: int) -> bool:
    """进程存活检测：Windows 下 os.kill(pid,0) 抛 ValueError 不可用，改用 ctypes OpenProcess
    （HANDLE 64 位，必须显式 restype，否则截断导致误判）"""
    if not pid:
        return False
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.CloseHandle.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if h:
            k32.CloseHandle(h)
            return True
        return False
    except Exception:
        # 非 Windows 兜底
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

def start_service() -> dict:
    """幂等启动：先抢实例锁，抢不到说明已有实例；抢到才写 state（enabled=true）"""
    if not acquire_instance_lock():
        st = _read_state()
        return {"ok": False, "already": True, "pid": st.get("pid"),
                "reason": "another instance holds lock"}
    st = _read_state()
    if st.get("enabled") and _pid_alive(st.get("pid")):
        return {"ok": True, "already": True, "pid": st["pid"]}
    pid = os.getpid()
    _write_state({"enabled": True, "pid": pid, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "batch": 0})
    chat_space.doubao_speak("📡 闲聊继续。刚才聊到哪儿，就顺着往下走，不用另起话题。")
    return {"ok": True, "already": False, "pid": pid}

def stop_service() -> dict:
    """优雅停止（由面板调用）：写 stop_requested + stop.signal 唤醒子进程 → 等进程退出（非轮询）"""
    st = _read_state()
    old_pid = st.get("pid") or 0
    st["stop_requested"] = True
    st["enabled"] = False
    st["pid"] = 0
    _write_state(st)
    chat_space.doubao_speak("🔕 闲聊服务暂停，这里先安静一会儿。")
    # 触发目录写入事件唤醒子进程（事件驱动，无需轮询）
    try:
        with open(STOP_SIGNAL, "w", encoding="utf-8") as f:
            f.write("stop\n")
    except Exception:
        pass
    # 等进程退出：进程句柄 + WaitForSingleObject（事件等待，非轮询）；超时兜底 TerminateProcess
    if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
        SYNCHRONIZE = 0x00100000
        PROCESS_TERMINATE = 0x0001
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.WaitForSingleObject.restype = ctypes.c_uint32
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.TerminateProcess.restype = ctypes.c_int
        k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.CloseHandle.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, int(old_pid))
        if h:
            # 等待窗口需覆盖子进程的巡检周期（10s），否则子进程来不及优雅退出会被强杀
            rc = k32.WaitForSingleObject(h, 35000)
            if rc != 0:
                k32.TerminateProcess(h, 0)
            k32.CloseHandle(h)
    # 幂等清理 stop 信号（双保险：子进程 _finish 已删；这里兜底防强杀残留）
    try:
        if os.path.exists(STOP_SIGNAL):
            os.remove(STOP_SIGNAL)
    except Exception:
        pass
    return {"ok": True}

def status() -> dict:
    st = _read_state()
    pid = st.get("pid") or 0
    running = bool(st.get("enabled") and _pid_alive(pid))
    # 2026-11-09：新进程启动要先预热向量库（本机实测 ~14s），这段时间 state 还是
    # pid=0，旧逻辑直接报「已停止」，用户以为没成功就反复点按钮，每点一次多拉一批
    # 进程。这里补一个「正在启动」中间态：开关为开、还没拿到 pid、且 starting_ts
    # 在 30s 内 → starting=True，让面板显示启动中而不是失败。
    starting = False
    if not running and st.get("enabled") and not pid:
        try:
            ts = float(st.get("starting_ts") or 0)
            starting = ts > 0 and (time.time() - ts) < 30
        except (TypeError, ValueError):
            starting = False
    return {"running": running, "starting": starting, "pid": pid,
            "started_at": st.get("started_at"), "batch": st.get("batch", 0)}

# ---------------- 面板侧包装（子进程拉起/停止） ----------------
import subprocess

LOG = os.path.join(os.path.dirname(STATE), "long_chat.log")
_CREATE_NO_WINDOW = 0x08000000

def long_chat_status() -> dict:
    return status()

def long_chat_start() -> dict:
    """面板启动：幂等由实例锁保证（不再用 pid=0 占位，避免并发 rerun 双开）"""
    st = _read_state()
    if instance_locked():
        return {"ok": True, "already": True, "pid": st.get("pid")}
    # 预热窗口：刚写过 starting_ts 的 30s 内视为「正在启动」，防并发 rerun 各拉一个
    # 2026-11-09：原为 10s，短于新进程预热向量库的 ~14s，窗口关掉之后到回填 pid
    # 之前用户再点仍会多拉一批，故与 status() 的启动态保持一致。
    try:
        if st.get("starting_ts") and time.time() - float(st["starting_ts"]) < 30:
            return {"ok": True, "already": True, "starting": True, "pid": st.get("pid")}
    except (TypeError, ValueError):
        pass
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _write_state({"enabled": True, "pid": 0, "started_at": "", "batch": 0,
                  "topic_idx": 0, "stop_requested": False, "starting_ts": time.time()})
    with open(LOG, "a", encoding="utf-8") as lf:
        p = subprocess.Popen(
            [sys.executable, "-m", "services.long_chat"],
            cwd=root,
            stdout=lf, stderr=lf,
            creationflags=_CREATE_NO_WINDOW,
        )
    # 等子进程真正持锁并回填 pid（最多 20s）；抢不到锁的子进程会自行退出
    # 2026-11-09：原为 5s。新进程要先建 ChromaKB 预热向量库，本机实测 14.3s，
    # 5s 时 state 还是 pid=0，面板仍显示「已停止」，用户会以为失败而重复点击。
    deadline = time.time() + 20.0
    pid = 0
    while time.time() < deadline:
        st = _read_state()
        pid = st.get("pid") or 0
        if pid and _pid_alive(pid):
            break
        time.sleep(0.1)
    return {"ok": True, "already": False, "pid": pid or p.pid}

def long_chat_stop() -> dict:
    return stop_service()

def ensure_running() -> dict:
    """自动恢复（开机/面板启动时调用）：state 为 enabled 且进程不在 → 拉起"""
    st = _read_state()
    if not st.get("enabled"):
        return {"ok": True, "skipped": True, "reason": "开关未开启"}
    if _pid_alive(st.get("pid")):
        return {"ok": True, "skipped": True, "reason": "已在运行"}
    # 开关开但进程没跑 → 恢复
    res = long_chat_start()
    return {"ok": res["ok"], "recovered": True, "pid": res.get("pid")}

class _OVERLAPPED(ctypes.Structure):
    """Windows OVERLAPPED 结构（异步 IO 用）"""
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", ctypes.c_uint32),
        ("OffsetHigh", ctypes.c_uint32),
        ("hEvent", ctypes.c_void_p),
    ]

def _wait_chat_event(timeout: float = 10.0) -> bool:
    """事件等待：异步 ReadDirectoryChangesW + WaitForSingleObject（阻塞等待，非轮询）。
    返回 True=目录写入事件触发；False=超时（调用方仍应巡检 chat.md，防止事件机制故障时服务静默死等）。
    说明：本机曾出现 ReadDirectoryChangesW 永久阻塞（安全软件拦截文件系统通知），
    故采用异步版 + 超时上限双保险：事件正常时秒回（零轮询），事件故障时 10s 巡检兜底。
    注意：ctypes windll 默认 restype=c_int，64 位 HANDLE 会被截断 → 必须显式声明签名。"""
    FILE_LIST_DIRECTORY = 0x0001
    FILE_SHARE = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OVERLAPPED = 0x40000000
    FILE_NOTIFY_CHANGE_LAST_WRITE = 0x0004
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k32.ReadDirectoryChangesW.restype = ctypes.c_int
    k32.ReadDirectoryChangesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.c_int, ctypes.c_uint32,
                                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p]
    k32.CreateEventW.restype = ctypes.c_void_p
    k32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    k32.WaitForSingleObject.restype = ctypes.c_uint32
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    hDir = k32.CreateFileW(
        CHAT_DIR, FILE_LIST_DIRECTORY, FILE_SHARE,
        None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, None)
    if not hDir or hDir == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
        time.sleep(1)  # 目录异常兜底（错误路径，非常规轮询）
        return False
    hEvent = k32.CreateEventW(None, False, False, None)
    if not hEvent:
        k32.CloseHandle(hDir)
        time.sleep(1)
        return False
    ov = _OVERLAPPED()
    ov.hEvent = hEvent
    try:
        buf = ctypes.create_string_buffer(4096)
        n = ctypes.c_uint32(0)
        ok = k32.ReadDirectoryChangesW(
            hDir, buf, len(buf), False, FILE_NOTIFY_CHANGE_LAST_WRITE,
            ctypes.byref(n), ctypes.byref(ov), None)
        if not ok:  # 异步提交失败（权限/被拦截）→ 不硬等，走超时巡检
            return False
        rc = k32.WaitForSingleObject(hEvent, int(timeout * 1000))
        return rc == 0  # WAIT_OBJECT_0 = 事件触发
    finally:
        k32.CloseHandle(hDir)  # 关闭句柄会取消未完成 IO，事件句柄随之失效
        if hEvent:
            k32.CloseHandle(hEvent)

def _last_line_info() -> tuple:
    """解析 chat.md 最后一条发言 -> (speaker, content)"""
    try:
        lines = open(CHAT_MD, encoding="utf-8").read().splitlines()
    except Exception:
        return "", ""
    for l in reversed(lines):
        s = l.strip()
        if not s:
            continue
        if "| " in s and "：" in s:
            body = s.split("| ", 1)[1]
            sp = body.split("：", 1)
            return sp[0].strip(), sp[1].strip() if len(sp) > 1 else ""
        return "", s
    return "", ""

def _last_real_chat_ts() -> float:
    """最后一条真实对话的时间戳（跳过 📡/🔕/📖 广播行），无则 0。

    启动判断不能用文件 mtime：服务自己播报的 📡 也会刷新 mtime，
    会让「已空闲很久」永远判成「刚刚聊过」。
    """
    try:
        lines = open(CHAT_MD, encoding="utf-8").read().splitlines()
    except Exception:
        return 0.0
    for l in reversed(lines):
        s = l.strip()
        if not s or "| " not in s or "：" not in s:
            continue
        head, body = s.split("| ", 1)
        sp, content = (body.split("：", 1) + [""])[:2]
        if sp.strip() == "豆包" and content.strip().startswith(("📡", "🔕", "📖")):
            continue
        try:
            return time.mktime(time.strptime(head.strip(), "%Y-%m-%d %H:%M"))
        except Exception:
            return 0.0
    return 0.0

def _finish(own_pid: int) -> None:
    global _idle_timer, _lock_fd
    if _idle_timer:
        try:
            _idle_timer.cancel()
        except Exception:
            pass
    # 清理 stop 信号文件（防下次启动误判）
    try:
        if os.path.exists(STOP_SIGNAL):
            os.remove(STOP_SIGNAL)
    except Exception:
        pass
    st = _read_state()
    if st.get("pid") == own_pid:
        st["enabled"] = False
        st["pid"] = 0
        _write_state(st)
    # 释放实例锁（进程退出时系统也会释放，这里显式兜底）
    if _lock_fd is not None:
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
    print("[long_chat] 已停止", flush=True)

_idle_timer = None
_topic_lock = threading.Lock()

def _open_topic() -> None:
    """主动开新话题（写 chat.md → 目录事件 → 主线程被唤醒后接龙）"""
    st = _read_state()
    if st.get("stop_requested"):
        return
    with _topic_lock:
        topic_idx = int(st.get("topic_idx", 0))
        topic = TOPICS[topic_idx % len(TOPICS)]
        st["topic_idx"] = topic_idx + 1
        _write_state(st)
    chat_space.doubao_speak(f"🎯 新话题轮：{topic}（接话题的接，想聊别的也随意）")

def _arm_idle_timer() -> None:
    global _idle_timer
    if _idle_timer:
        try:
            _idle_timer.cancel()
        except Exception:
            pass
    _idle_timer = threading.Timer(IDLE_TOPIC_SEC, _open_topic)
    _idle_timer.daemon = True
    _idle_timer.start()

def _warm_embedding() -> None:
    """后台预热向量库：避免第一轮接龙卡在 MiniLM 冷启动 / HF 超时重试（实测可达 2 分钟）"""
    try:
        from core.chroma_kb import get_chroma_kb
        get_chroma_kb()
        print("[long_chat] 向量库预热完成", flush=True)
    except Exception as e:
        print(f"[long_chat] 向量库预热失败: {str(e)[:120]}", flush=True)


def run_forever() -> None:
    """事件驱动主循环：阻塞等目录事件 → 判断是否接龙 → 空闲闹钟兜底开话题"""
    started = start_service()
    if not started.get("ok"):
        print(f"[long_chat] 已有实例持锁，本进程退出（pid={os.getpid()}）", flush=True)
        return
    own_pid = os.getpid()
    threading.Thread(target=_warm_embedding, daemon=True).start()
    # 启动时顺手做一次闲聊归档（幂等；低于阈值会直接跳过）
    try:
        from services.chat_archive import archive as _chat_archive
        _chat_archive()
    except Exception as e:
        print(f"[long_chat] 闲聊归档跳过: {str(e)[:100]}", flush=True)

    # 启动时补刷状态摘要。断线期间积压的发言如果不在这里补，重启后模型读到的是
    # 断开前的旧状态——「断了几小时就接不上」的根因就在这。
    # 放后台线程：单次摘要要调本地小模型，几十秒起步，不能挡主循环。
    def _refresh_state_on_start():
        time.sleep(5)  # 让 _warm_embedding 先换入模型，避免两边同时抢显存
        try:
            with open(CHAT_MD, encoding="utf-8") as f:
                total = len([l for l in f.read().splitlines() if l.strip()])
            start = int(chat_space._load_summary_state().get("line", 0) or 0)
        except Exception:
            total, start = 0, 0
        pending = max(0, total - start)
        rounds = max(1, min(8, (pending + 39) // 40))
        for i in range(rounds):
            try:
                r = chat_space.maybe_summarize(force=True)
                if not r.get("ok"):
                    print(f"[long_chat] 启动补刷状态 {i+1}/{rounds}: {r.get('reason')}", flush=True)
                    break
                print(f"[long_chat] 启动补刷状态 {i+1}/{rounds} 完成", flush=True)
            except Exception as e:
                print(f"[long_chat] 启动补刷状态异常: {str(e)[:100]}", flush=True)
                break

    threading.Thread(target=_refresh_state_on_start, daemon=True).start()
    last_reply_ts = 0.0
    count_since_topic = 0
    topic_ts = time.time()
    # 自动恢复：启动时若最后一条真实对话已过去很久，立即开话题（不等闹钟）
    # 不能看文件 mtime —— 上面的 📡 播报刚把它刷成“现在”
    try:
        _last = _last_real_chat_ts()
        if _last and time.time() - _last > IDLE_TOPIC_SEC:
            _open_topic()
        elif _last:
            # 刚聊过（gap < 空闲阈值）：不主动接一轮的话，下面的 _wait_chat_event()
            # 会一直阻塞——📡 播报的写入发生在进入循环之前，那次事件已经错过了，
            # 之后没有新写入就永远等不到。所以这里必须自己踢一脚。
            try:
                _r = chat_space.chat_round(mode="chat")
                last_reply_ts = time.time()
                count_since_topic += 1
                print(f"[long_chat] 启动接一轮 {len(_r['round'])} 条", flush=True)
            except Exception as e:
                print(f"[long_chat] 启动接一轮异常: {str(e)[:120]}", flush=True)
    except Exception:
        pass
    _arm_idle_timer()
    try:
        while True:
            _wait_chat_event()
            # 停止信号（stop_service 写入 stop.signal 触发目录事件唤醒）
            if _read_state().get("stop_requested"):
                _finish(own_pid)
                return
            try:
                if os.path.exists(STOP_SIGNAL):
                    os.remove(STOP_SIGNAL)
                    _finish(own_pid)
                    return
            except Exception:
                pass
            now = time.time()
            mtime = os.path.getmtime(CHAT_MD) if os.path.exists(CHAT_MD) else 0
            gap = now - mtime
            speaker, content = _last_line_info()
            # 服务广播（📡 状态 / 🔕 停止 / 📖 读书结果 / 📷 看图结果）不接
            if speaker == "豆包" and content.startswith(("📡", "🔕", "📖", "📷")):
                continue
            # 冷却：距上次回应太近 → 跳过（防自触发事件风暴）
            if now - last_reply_ts < THINK_MIN:
                continue
            # 批次休息窗口：连续回应超限且距开话题不久 → 跳过
            if count_since_topic >= BATCH_MAX and now - topic_ts < REST_SEC:
                continue
            # 冷场兜底：最后发言（豆包或模型）已很久没有动静 → 开新话题
            if gap > IDLE_TOPIC_SEC:
                _open_topic()
                topic_ts = now
                count_since_topic = 0
                continue
            # 有新鲜发言（豆包话题/示范 或 模型接龙）→ 回应
            try:
                r = chat_space.chat_round(mode="chat")
                last_reply_ts = time.time()
                count_since_topic += 1
                print(f"[long_chat] 回应 {len(r['round'])} 条", flush=True)
                # 批次结束（进入休息窗口）→ 双层摘要（短摘要 + 低频长摘要，失败静默不阻塞）
                if count_since_topic >= BATCH_MAX:
                    chat_space.maybe_summarize()
                    chat_space.maybe_long_summarize()
                    # 群聊当老师：云端教师（小深）定期讲评，纠错/给标准解（内部频率闸门控）
                    try:
                        tr = chat_space.maybe_teacher_review()
                        if tr.get("pending_for_doubao"):
                            print("[long_chat] 已登记待豆包讲评素材（等豆包在场点评）", flush=True)
                        elif tr.get("ok"):
                            print("[long_chat] 教师讲评已发布", flush=True)
                        elif not tr.get("skipped"):
                            print(f"[long_chat] 教师讲评跳过: {tr.get('error')}", flush=True)
                    except Exception as e:
                        print(f"[long_chat] 教师讲评异常: {str(e)[:120]}", flush=True)
            except Exception as e:
                print(f"[long_chat] 轮次异常: {str(e)[:120]}", flush=True)
            _arm_idle_timer()
    except KeyboardInterrupt:
        _finish(own_pid)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop_service()
    else:
        run_forever()