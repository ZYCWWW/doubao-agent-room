# core/desktop_characters.py
"""
桌面人物：tkinter 独立进程显示模型角色（最多 3 个上台，其余在想象空间）
- 通信：sandbox/desktop_characters/state.json（面板/闲聊引擎写入，窗口进程轮询刷新）
- state: {"active": [{"name","emoji","msg","color"}...], "in_space": ["Qwen", ...],
          "running": true, "stop": false}
- 启动：start_desktop()（subprocess 独立进程，不阻塞）；停止：stop_desktop()（写 state.stop=true）
"""
import ctypes
import json
import os
import subprocess
import sys
import time

_STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "sandbox", "desktop_characters")
_STATE_FILE = os.path.join(_STATE_DIR, "state.json")
_MODEL_META = {
    "deepseekr1q7b:latest": {"name": "小深", "emoji": "🧠", "color": "#1e6fd9"},
    "llama3.1:8b": {"name": "Llama", "emoji": "🦙", "color": "#d97a1e"},
    "qwen3.5:4b": {"name": "Qwen", "emoji": "🌀", "color": "#1e9e5a"},
    "doubao": {"name": "豆包", "emoji": "🧑‍💼", "color": "#8b5bd2"},
    "user": {"name": "你", "emoji": "🧑", "color": "#6b5bd2"},
}
MAX_DESKTOP = 4
_PROCS: list = []


def _norm(model: str) -> str:
    m = (model or "").lower()
    for k in _MODEL_META:
        if k in m or m in k:
            return k
    return "user" if m == "user" else "deepseekr1q7b:latest"


def meta(model: str) -> dict:
    k = _norm(model)
    return _MODEL_META.get(k, {"name": k, "emoji": "🤖", "color": "#888"})


def update_state(active: list, in_space: list = None, extra_notes: str = "") -> dict:
    """写入桌面人物状态；active: [{model, msg}]（≤3 个上台，多出的进 in_space）"""
    os.makedirs(_STATE_DIR, exist_ok=True)
    act = []
    for item in active[:MAX_DESKTOP]:
        m = meta(item.get("model", ""))
        act.append({"name": m["name"], "emoji": m["emoji"],
                    "color": m["color"],
                    "msg": str(item.get("msg", ""))[:120]})
    in_sp = []
    for model in (in_space or []):
        m = meta(model)
        in_sp.append(f"{m['emoji']} {m['name']}")
    # 超过 3 个的活跃者自动回到空间
    overflow = active[MAX_DESKTOP:]
    for item in overflow:
        m = meta(item.get("model", ""))
        in_sp.append(f"{m['emoji']} {m['name']}")
    state = {"active": act, "in_space": in_sp,
             "notes": extra_notes[:80], "ts": time.time(),
             "running": True, "stop": False}
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return state


def _write_stop() -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    state = {"active": [], "in_space": [], "notes": "", "ts": time.time(),
             "running": False, "stop": True}
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _clear_stop() -> None:
    """清除残留的 stop 标记（上次关闭后遗留会导致窗口进程启动即退出）"""
    try:
        if not os.path.exists(_STATE_FILE):
            return
        with open(_STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("stop"):
            st.pop("stop", None)
            st["running"] = False
            st["ts"] = time.time()
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
    except Exception:
        pass


def _pid_alive(pid) -> bool:
    """检查 pid 是否存活（Windows os.kill(pid,0)）"""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True


def start_desktop() -> int:
    """启动桌面人物独立进程（tkinter 窗口）；已运行则跳过，返回 pid"""
    global _PROCS
    if _PROCS and any(p.poll() is None for p in _PROCS):
        return _PROCS[0].pid or 0
    # 跨进程查重：桌面进程已在跑（pid 记录于 state）则复用，避免多实例双窗口
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            _st = json.load(f)
        if _pid_alive(_st.get("desktop_pid")):
            return _st["desktop_pid"]
    except Exception:
        pass
    _clear_stop()
    _write_state_init()
    py = sys.executable
    os.makedirs(_STATE_DIR, exist_ok=True)
    logf = open(os.path.join(_STATE_DIR, "pet.log"), "a", encoding="utf-8")
    p = subprocess.Popen([py, "-m", "core.desktop_characters"],
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=logf, stderr=logf)
    _PROCS = [p]
    return p.pid or 0


def stop_desktop() -> None:
    """请求窗口进程退出（写 stop 标记）"""
    _write_stop()
    global _PROCS
    for p in _PROCS:
        try:
            p.terminate()
        except Exception:
            pass
    _PROCS = []


def _write_state_init() -> None:
    if not os.path.exists(_STATE_FILE):
        update_state([], ["小深", "Llama", "Qwen 正在想象空间等待"])


def ensure_running() -> int:
    """进程守护：桌面人物挂了自动重启（供 idle/dashboard 定时调用）"""
    try:
        return start_desktop()
    except Exception as e:
        import sys as _s
        print(f"桌面人物守护异常: {e}", file=_s.stderr)
        return 0


# ---------------- 负载检测（ctypes Windows API，无第三方依赖） ----------------
class _MemStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _Load:
    last_times = None

    @staticmethod
    def cpu_usage() -> float:
        """CPU 占用率（GetSystemTimes 两次采样差值）；首次采样返回 0"""
        k, u, i = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        try:
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
            cur = (i.value, k.value, u.value)
        except Exception:
            return 0.0
        if _Load.last_times:
            di, dk, du = (cur[0] - _Load.last_times[0], cur[1] - _Load.last_times[1],
                          cur[2] - _Load.last_times[2])
            total = dk + du
            _Load.last_times = cur
            return 100.0 * (total - di) / total if total > 0 else 0.0
        _Load.last_times = cur
        return 0.0

    @staticmethod
    def mem_usage() -> float:
        """内存占用百分比（GlobalMemoryStatusEx）"""
        m = _MemStatusEx(); m.dwLength = ctypes.sizeof(_MemStatusEx)
        try:
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return float(m.dwMemoryLoad)
        except Exception:
            return 0.0

    @staticmethod
    def snapshot() -> dict:
        return {"cpu": round(_Load.cpu_usage(), 1),
                "mem": round(_Load.mem_usage(), 1),
                "sleep": False}


def _gui_main() -> None:
    """tkinter 窗口主循环（独立进程入口）：动态桌宠 + 负载自适应休眠
    桌宠样式：小卡片（不挡视线）+ 半透明 + 右侧贴边纵向排列"""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    # 记录本进程 pid 到 state（供 start_desktop 跨进程查重）
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            _st = json.load(f)
        _st["desktop_pid"] = os.getpid()
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_st, f, ensure_ascii=False)
    except Exception:
        pass
    wins = []
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    PET_W, PET_H = 128, 112  # 桌宠卡片尺寸（小巧不挡视线）
    base_x = sw - PET_W - 16  # 贴屏幕右侧
    for i in range(MAX_DESKTOP):
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.92)  # 半透明，降低遮挡感
        w.geometry(f"{PET_W}x{PET_H}+{base_x}+{sh - 40 - (MAX_DESKTOP - i) * (PET_H + 6)}")
        w.configure(bg="#15151c")
        wins.append(w)
    # 状态条（右下角，贴近桌宠列）：想象空间中的模型 + 休眠状态
    st_w = tk.Toplevel(root)
    st_w.overrideredirect(True)
    st_w.attributes("-topmost", True)
    st_w.attributes("-alpha", 0.85)
    st_w.geometry(f"168x96+{base_x - 178}+{sh - 200}")
    st_w.configure(bg="#101016")
    st_lbl = tk.Label(st_w, bg="#101016", fg="#aaa", font=("Microsoft YaHei", 9),
                      justify="left", anchor="nw", text="🌌 想象空间：")
    st_lbl.pack(fill="both", expand=True, padx=6, pady=4)
    st_lbl.place(x=4, y=4, relwidth=0.96)

    CHAR_FILE = os.path.join(os.path.dirname(_STATE_DIR), "chat_space", "characters.json")
    sleeping = {"flag": False}
    typing = {"idx": 0}
    _cache = {"sig": []}  # 窗口内容签名：不变则跳过重建（省 CPU）

    def _read_state():
        try:
            with open(_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _read_chars():
        try:
            with open(CHAR_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _update_float():
        """动态：对可见窗口做轻微上下浮动（呼吸感），基于当前位置 ±2 像素"""
        import math
        for i, w in enumerate(wins):
            if w.winfo_ismapped():
                try:
                    _g = w.geometry().split("+")
                    cur_y = int(_g[2]) if len(_g) > 2 else 0
                    base = cur_y + int(2 * math.sin(i + time.time() / 2))
                    w.geometry(f"+{_g[1]}+{base}")
                except Exception:
                    pass

    def _refresh():
        s = _read_state()
        if s and s.get("stop"):
            root.destroy()
            return
        # 负载自适应：高负荷 → 全部休眠回空间；恢复 → 重新上台
        # （16GB 机器 Ollama 多模型常驻，内存阈值放宽避免人物永久休眠）
        load = _Load.snapshot()
        if load["mem"] > 96 or load["cpu"] > 92:
            sleeping["flag"] = True
        elif load["mem"] < 92 and load["cpu"] < 85:
            sleeping["flag"] = False
        chars = _read_chars()
        items = (s or {}).get("active", [])

        def _key(it):
            ch = chars.get("doubao" if it.get("name") == "豆包" else
                           {"🧠 小深": "deepseekr1q7b:latest",
                            "🧠 DeepSeek": "deepseekr1q7b:latest",
                            "🦙 Llama": "llama3.1:8b", "🌀 Qwen": "qwen3.5:4b"}.get(
                               f"{it.get('emoji', '')} {it.get('name', '')}", ""), {})
            return (it.get("name", ""), it.get("msg", "")[:120],
                    ch.get("emoji", ""), ch.get("color", ""), sleeping["flag"])

        sig = [_key(i) for i in items]
        if sig != _cache["sig"]:
            _cache["sig"] = sig
            typing["idx"] = 0
        elif not sleeping["flag"]:
            # 内容未变且非休眠：只做打字机与浮动，不重建控件
            _update_float()
            root.after(2000, _refresh)
            return
        for i, w in enumerate(wins):
            for child in w.winfo_children():
                child.destroy()
            if sleeping["flag"]:
                w.withdraw()
                continue
            if i < len(items):
                it = items[i]
                ch = chars.get("doubao" if it.get("name") == "豆包" else
                               {"🧠 小深": "deepseekr1q7b:latest",
                                "🧠 DeepSeek": "deepseekr1q7b:latest",
                                "🦙 Llama": "llama3.1:8b", "🌀 Qwen": "qwen3.5:4b"}.get(
                                   f"{it.get('emoji', '')} {it.get('name', '')}", ""), {})
                if not ch:
                    ch = {"emoji": it.get("emoji", "🤖"), "color": it.get("color", "#888"),
                          "intro": "", "catchphrase": ""}
                # 桌宠布局：大头像 emoji + 名字 + 小气泡发言（不挡视线）
                emoji = ch.get("emoji", it.get("emoji", "🤖"))[:1]
                tk.Label(w, text=emoji, bg="#15151c",
                         font=("Segoe UI Emoji", 26), anchor="center").pack(fill="x", pady=(6, 0))
                tk.Label(w, text=it.get("name", ""), bg="#15151c",
                         fg=ch.get("color", it.get("color", "#888")),
                         font=("Microsoft YaHei", 10, "bold"), anchor="center").pack(fill="x")
                full = it.get("msg", "…")
                typing["idx"] = min(typing["idx"] + 3, len(full))
                shown = full[:typing["idx"]]
                if typing["idx"] >= len(full):
                    typing["idx"] = 0
                if shown:
                    tk.Label(w, text=shown[-60:], bg="#15151c", fg="#eee",
                             font=("Microsoft YaHei", 8), justify="left",
                             anchor="nw", wraplength=116).pack(fill="x", padx=6, pady=(2, 0))
                if ch.get("catchphrase"):
                    tk.Label(w, text=f"“{ch['catchphrase'][:14]}”", bg="#15151c",
                             fg=ch.get("color", "#888"), font=("Microsoft YaHei", 7, "italic"),
                             anchor="center").pack(fill="x", pady=(2, 4))
                w.deiconify()
            else:
                w.withdraw()
        _update_float()
        in_sp = (s or {}).get("in_space", [])
        head_txt = "💤 负荷较高，已回空间休息（负荷降低自动回来）" if sleeping["flag"] else "🌌 想象空间："
        st_lbl.config(text=head_txt + "\n" + ("\n".join(in_sp) if in_sp else "（都在台上）"))
        root.after(2000, _refresh)

    root.after(1000, _refresh)
    root.mainloop()


if __name__ == "__main__":
    _gui_main()
