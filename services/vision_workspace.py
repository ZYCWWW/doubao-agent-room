"""
视觉工作台（独立原生窗口）

把用户桌面上的软件（记事本、画图……）拖进这个窗口的「舞台」，
用 SetParent 真正嵌进来：舞台上看到的就是那个软件本身。
模型通过本地 HTTP 接口只截舞台、只点舞台，坐标按 0~1000 归一化。

为什么必须是独立原生窗口：Streamlit 页面（浏览器）没有 HWND，
当不了窗口容器，嵌不进去。

线程约定（重要）：Tk 控件只能在主线程动。HTTP 线程与后台线程一律通过
`_ui()` 把界面更新丢回主线程；舞台几何缓存在 `_stage_rect` / `_stage_hwnd`，
供任意线程读取，避免在子线程调用 winfo_*。

界面分三块：
- 左栏：软件地址管理器（config/software_registry.json，可增删）+ 可嵌入窗口列表
- 右侧：舞台（深色底，嵌入的软件铺满它）
- 底部：状态条（当前附着窗口 / 控制端口）

对外接口（只绑 127.0.0.1）：
    GET  /state          当前状态
    GET  /software       软件地址管理器条目
    GET  /windows        当前可嵌入的窗口
    POST /launch         {"name": "记事本"}
    POST /attach         {"title": "...", "hwnd": 0x...}
    POST /detach         {}
    POST /shot           -> {"path": "..."}
    POST /click          {"x":0-1000,"y":0-1000,"button":"left","clicks":1}
    POST /drag           {"x1":..,"y1":..,"x2":..,"y2":..,"duration":0.4}
    POST /type           {"text": "..."}
    POST /key            {"key": "enter"}
    POST /scroll         {"delta": 2}
    POST /quit           {}
"""
import ctypes
import json
import os
import sys
import threading
import time
import uuid

# 视觉工作台依赖 Win32 窗口嵌入 + tkinter，只能在 Windows 桌面上跑。
# 其他平台直接给出可读提示并正常退出，避免面板点了按钮后子进程抛 AttributeError。
if sys.platform != "win32":
    sys.stderr.write("视觉工作台仅支持 Windows 桌面环境（依赖 Win32 窗口嵌入）。\n")
    sys.exit(0)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tkinter as tk
from tkinter import messagebox, simpledialog

from core.config_loader import config_loader
from core.desktop import win32_api as wa
from core.logger import root_logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(ROOT, "config", "software_registry.json")
PORT_FILE = os.path.join(ROOT, "logs", "vision_workspace.port")
LOG_FILE = os.path.join(ROOT, "logs", "vision_workspace.out.log")

SANDBOX = config_loader["sandbox"]["root_path"]
SHOT_DIR = os.path.join(SANDBOX, "workspace")
# 舞台截图只留最近这么多张（模型每调一次 workspace_look 就落一张，不轮转会无限涨）
SHOT_KEEP = 30


def prune_shots(dir_path: str, keep: int = SHOT_KEEP, prefix: str = "ws_") -> int:
    """按修改时间只保留最近 keep 张舞台截图，返回实际删除的数量。

    dir_path 不存在或读不到就当没东西可删，不抛异常（调用方在截图主链路上）。
    """
    try:
        files = [os.path.join(dir_path, f) for f in os.listdir(dir_path)
                 if f.startswith(prefix) and f.lower().endswith(".png")]
    except OSError:
        return 0
    if len(files) <= keep:
        return 0
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    removed = 0
    for p in files[keep:]:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed

DEFAULT_REGISTRY = {
    "items": [
        {"name": "记事本", "target": "notepad.exe", "args": "",
         "title_hint": "记事本", "cls_hint": "Notepad"},
        {"name": "画图", "target": "shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App",
         "args": "", "title_hint": "画图", "cls_hint": ""},
        {"name": "计算器", "target": "apps\\calculator.py",
         "args": "", "title_hint": "计算器·内置", "cls_hint": "AgentCalcWnd"},
    ]
}

START_PORT = 8899
MAX_PORT = 8910


def load_registry() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        save_registry(DEFAULT_REGISTRY)
        return json.loads(json.dumps(DEFAULT_REGISTRY))
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("items", [])
        return data
    except Exception as e:
        root_logger.warning(f"[视觉工作台] 软件地址管理器读取失败，用默认值：{e}")
        return json.loads(json.dumps(DEFAULT_REGISTRY))


def save_registry(data: dict):
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY_PATH)


def _set_clipboard_text(text: str) -> bool:
    """把文本塞进剪贴板（中文输入走 ctrl+v，pyautogui 的 typewrite 只支持 ASCII）"""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    try:
        data = text.encode("utf-16-le") + b"\x00\x00"
        if not u32.OpenClipboard(0):
            return False
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            ptr = k32.GlobalLock(h)
            if not ptr:
                k32.GlobalFree(h)
                return False
            ctypes.memmove(ptr, data, len(data))
            k32.GlobalUnlock(h)
            u32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h))
        finally:
            u32.CloseClipboard()
        return True
    except Exception as e:
        root_logger.warning(f"[视觉工作台] 剪贴板写入失败：{e}")
        return False


class Workspace:
    """工作台单例：Tk 界面 + 嵌入状态 + 舞台坐标系"""

    def __init__(self):
        wa.dpi_aware()
        self.registry = load_registry()
        self.attached = None          # {"hwnd","title","cls","pid","old_style","old_rect"}
        self._prev_down = False
        self._press_pos = None
        self._press_hwnd = None
        self._closing = False
        self.port = None
        self.httpd = None
        self._wins = []
        self._stage_hwnd = None
        self._stage_rect = (0, 0, 1, 1)

        self.root = tk.Tk()
        self.root.title("视觉工作台 — 把软件拖进舞台")
        self.root.geometry("1600x985+8+8")
        self.root.minsize(1180, 840)
        self.root.configure(bg="#141414")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root_hwnd = self.root.winfo_id()
        self._build_ui()
        self.root.update_idletasks()
        # winfo_id 拿到的是 Tk 内层子窗口，比较窗口归属时要用真正的顶层
        self.root_hwnd = wa.root_of(self.root_hwnd)
        self._sync_stage()
        self._start_http()
        self.root.after(80, self._poll_drop)
        self.root.after(400, self.refresh_window_list)

    # ---------------- 线程工具 ----------------

    def _ui(self, fn, *a, **kw):
        """把界面操作丢回主线程（HTTP/后台线程里不能直接碰 Tk 控件）"""
        self.root.after(0, lambda: fn(*a, **kw))

    def _in_main(self) -> bool:
        return threading.current_thread() is threading.main_thread()

    # ---------------- 界面 ----------------

    def _build_ui(self):
        bar = tk.Frame(self.root, bg="#0f1620", height=46)
        bar.pack(side="top", fill="x")
        tk.Label(bar, text="视觉工作台", bg="#0f1620", fg="#e8f0ff",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", padx=14, pady=8)
        tk.Label(bar, text="把桌面上的软件窗口拖到右侧舞台里，模型就能看见并只在其中操作",
                 bg="#0f1620", fg="#7f8ea3", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=6)

        body = tk.Frame(self.root, bg="#141414")
        body.pack(side="top", fill="both", expand=True)

        left = tk.Frame(body, bg="#181c22", width=286)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # 软件地址管理器
        tk.Label(left, text="软件地址管理器", bg="#181c22", fg="#d7e2f2",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(left, text="登记过的软件，一键启动并自动嵌入", bg="#181c22", fg="#6f7f93",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12)

        self.sw_list = tk.Listbox(left, height=7, bg="#0f1216", fg="#cfd9e6",
                                  selectbackground="#27508a", activestyle="none",
                                  font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0)
        self.sw_list.pack(fill="x", padx=10, pady=6)

        row1 = tk.Frame(left, bg="#181c22")
        row1.pack(fill="x", padx=10)
        self._btn(row1, "启动", self.launch_selected).pack(side="left")
        self._btn(row1, "新增", self.add_software).pack(side="left", padx=4)
        self._btn(row1, "删除", self.del_software).pack(side="left")

        tk.Frame(left, bg="#242a33", height=1).pack(fill="x", pady=10)

        # 可嵌入窗口
        tk.Label(left, text="桌面窗口", bg="#181c22", fg="#d7e2f2",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        tk.Label(left, text="选中一个嵌入舞台（也可以直接从桌面拖进来）", bg="#181c22", fg="#6f7f93",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12)

        self.win_list = tk.Listbox(left, height=12, bg="#0f1216", fg="#cfd9e6",
                                   selectbackground="#27508a", activestyle="none",
                                   font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0)
        self.win_list.pack(fill="x", padx=10, pady=6)

        row2 = tk.Frame(left, bg="#181c22")
        row2.pack(fill="x", padx=10)
        self._btn(row2, "刷新", self.refresh_window_list).pack(side="left")
        self._btn(row2, "嵌入选中", self.attach_selected).pack(side="left", padx=4)
        self._btn(row2, "解除", self.detach).pack(side="left")

        tk.Frame(left, bg="#242a33", height=1).pack(fill="x", pady=10)
        self._btn(left, "截一张舞台图（给模型看）", self.shot_ui).pack(fill="x", padx=10)

        # 舞台
        right = tk.Frame(body, bg="#141414")
        right.pack(side="left", fill="both", expand=True)
        self.stage = tk.Frame(right, bg="#0b0b0b", highlightbackground="#2b323c",
                              highlightthickness=1)
        self.stage.pack(fill="both", expand=True, padx=10, pady=10)
        self.stage.bind("<Configure>", self._on_stage_configure)
        self.stage_hint = tk.Label(self.stage,
                                   text="把桌面上的软件窗口拖到这里\n（拖动时按住窗口标题栏，松手即可）",
                                   bg="#0b0b0b", fg="#4a5666",
                                   font=("Microsoft YaHei UI", 12))
        self.stage_hint.place(relx=0.5, rely=0.5, anchor="center")

        self.status = tk.Label(self.root, text="就绪", bg="#0f1620", fg="#8fa0b5", anchor="w",
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(side="bottom", fill="x")

        self.refresh_software_list()

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, bg="#22303f", fg="#dce7f5",
                         activebackground="#2d3f52", activeforeground="#ffffff",
                         relief="flat", bd=0,
                         font=("Microsoft YaHei UI", 9), padx=9, pady=3, cursor="hand2")

    def refresh_software_list(self):
        self.sw_list.delete(0, "end")
        for it in self.registry.get("items", []):
            self.sw_list.insert("end", it.get("name", "?"))

    # ---------------- 软件地址管理器 ----------------

    def launch_selected(self):
        sel = self.sw_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个软件")
            return
        item = self.registry["items"][sel[0]]
        if self.attached:
            self.detach()
        res = wa.launch(item.get("target", ""), item.get("args", ""))
        if not res.get("ok"):
            messagebox.showerror("启动失败", res.get("reason", "未知原因"))
            return
        self._set_status(f"已启动「{item['name']}」，正在等窗口出现…")
        threading.Thread(target=self._auto_attach_after_launch, args=(item,), daemon=True).start()

    def _auto_attach_after_launch(self, item):
        hwnd = None
        deadline = time.time() + 15
        while time.time() < deadline and hwnd is None:
            if item.get("title_hint"):
                hwnd = wa.find_window(title_has=item["title_hint"])
            if hwnd is None and item.get("cls_hint"):
                hwnd = wa.find_window(cls_has=item["cls_hint"])
            time.sleep(0.4)
        if hwnd:
            self._ui(self._attach_hwnd, hwnd, True)
        else:
            self._set_status(f"「{item['name']}」的窗口没等到，可以点「刷新」手动嵌入")

    def add_software(self):
        name = simpledialog.askstring("新增软件", "名称（如 记事本）：", parent=self.root)
        if not name:
            return
        target = simpledialog.askstring("新增软件", "启动目标（exe 路径或 shell: URI）：", parent=self.root)
        if not target:
            return
        hint = simpledialog.askstring("新增软件", "窗口标题包含（可留空，用于自动定位窗口）：",
                                      parent=self.root) or ""
        item = {"name": name.strip(), "target": target.strip(), "args": "",
                "title_hint": hint.strip(), "cls_hint": ""}
        self.registry["items"].append(item)
        save_registry(self.registry)
        self.refresh_software_list()
        self._set_status(f"已登记「{item['name']}」")

    def del_software(self):
        sel = self.sw_list.curselection()
        if not sel:
            return
        item = self.registry["items"][sel[0]]
        if messagebox.askyesno("删除", f"从软件地址管理器移除「{item['name']}」？"):
            self.registry["items"].pop(sel[0])
            save_registry(self.registry)
            self.refresh_software_list()

    # ---------------- 窗口列表与嵌入 ----------------

    def refresh_window_list(self):
        if not self._in_main():
            self._ui(self.refresh_window_list)
            return
        self._wins = wa.list_windows(exclude_pids=[os.getpid()])
        self.win_list.delete(0, "end")
        for w in self._wins:
            tag = "  [已附着]" if (self.attached and w["hwnd"] == self.attached["hwnd"]) else ""
            self.win_list.insert("end", f"{w['title'][:34]}{tag}")

    def attach_selected(self):
        sel = self.win_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个窗口")
            return
        self._attach_hwnd(self._wins[sel[0]]["hwnd"])

    def _fit_size(self, hwnd):
        """嵌入尺寸：缩到不超过舞台，但绝不放大。

        比舞台大就缩到舞台（否则溢出舞台的部分不可见却能被点到）；
        比舞台小就保持自身尺寸，不去拉伸它——拉伸会让应用布局错位。
        """
        r = self._stage_rect
        sw, sh = max(200, r[2] - r[0]), max(150, r[3] - r[1])
        wr = wa.get_rect(hwnd) or (0, 0, sw, sh)
        ww, wh = max(200, wr[2] - wr[0]), max(150, wr[3] - wr[1])
        return min(ww, sw), min(wh, sh)

    def _attach_hwnd(self, hwnd, auto=False):
        if self._closing or not hwnd:
            return False
        if self.attached and self.attached.get("hwnd") != int(hwnd):
            self.detach()
        w, h = self._fit_size(hwnd)
        res = wa.attach(int(hwnd), self._stage_hwnd, 0, 0, w, h)
        if not res.get("ok"):
            root_logger.warning(f"[视觉工作台] 嵌入失败 hwnd={hwnd} title={(wa.window_info(hwnd) or {}).get('title')!r} "
                                f"reason={res.get('reason')}")
            self._set_status("嵌入失败：" + res.get("reason", ""))
            if not auto:
                self._ui(messagebox.showerror, "嵌入失败", res.get("reason", "未知原因"))
            return False
        self.attached = res
        self._ui(self.stage_hint.place_forget)
        self._set_status(f"已嵌入「{res['title']}」（{res['cls']}），舞台内即该软件")
        self._ui(self.refresh_window_list)
        root_logger.info(f"[视觉工作台] 附着 {res['title']!r} hwnd=0x{res['hwnd']:X} size={w}x{h}")
        return True

    def detach(self):
        if not self.attached:
            return {"ok": True}
        a = self.attached
        self.attached = None
        res = wa.detach(a["hwnd"], a.get("old_style"), a.get("old_rect"))
        self._ui(lambda: self.stage_hint.place(relx=0.5, rely=0.5, anchor="center"))
        self._set_status("已解除嵌入")
        self._ui(self.refresh_window_list)
        return res

    def _sync_stage(self):
        """在主线程缓存舞台的 HWND 与屏幕矩形，供任意线程读取"""
        if not self._in_main():
            self._ui(self._sync_stage)
            return
        try:
            self.root.update_idletasks()
            self._stage_hwnd = self.stage.winfo_id()
            r = wa.client_rect_screen(self._stage_hwnd)
            if not r or r[2] - r[0] < 20 or r[3] - r[1] < 20:
                x = self.stage.winfo_rootx()
                y = self.stage.winfo_rooty()
                w = max(1, self.stage.winfo_width())
                h = max(1, self.stage.winfo_height())
                r = (x, y, x + w, y + h)
            self._stage_rect = r
        except Exception as e:
            root_logger.warning(f"[视觉工作台] 舞台几何同步失败：{e}")

    def _on_stage_configure(self, _evt=None):
        # 嵌入的软件保持自己的尺寸与 (0,0) 位置，舞台变化只影响可见范围，
        # 不做 MoveWindow —— 反复改尺寸会让 UWP 内部布局错位
        self._sync_stage()

    def _poll_drop(self):
        """拖放检测：拖外部窗口时鼠标被目标窗口捕获，Tk 收不到事件，
        所以轮询全局按键状态——在舞台内松手就取该点窗口嵌入"""
        if self._closing:
            return
        try:
            down = wa.left_button_down()
            if down != self._prev_down:
                # 只在状态翻转时打印，便于排查"从桌面拖入"没生效
                root_logger.info(f"[拖放] down={down} cursor={wa.cursor_pos()} "
                                 f"press={self._press_pos} stage={self._stage_rect} "
                                 f"host={wa.get_rect(self.root_hwnd)}")
            if down and not self._prev_down:
                self._press_pos = wa.cursor_pos()
                # 按下当时的窗口必须当场记下：拖动过程中窗口跟着光标走，
                # 松手后再回查按下点坐标，那里已经不是它了（而是底下的遮挡物）
                self._press_hwnd = wa.window_at_point(*self._press_pos)
            if self._prev_down and not down:
                px, py = self._press_pos or (0, 0)
                x, y = wa.cursor_pos()
                r = self._stage_rect
                host = wa.get_rect(self.root_hwnd) or (0, 0, 0, 0)
                # 只认「从工作台外面按下、在舞台里松手」= 从桌面拖进来。
                # 否则模型/用户在舞台内操作软件（画图里拖笔画线）也会被当成拖入，
                # 把当前软件换掉。
                dropped_in = not (host[0] <= px <= host[2] and host[1] <= py <= host[3])
                if dropped_in and r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                    # 候选优先取「按下时那个窗口」：拖的时候窗口跟着光标走，
                    # 松手点上最上层的常常是遮挡物（全屏置顶窗口），
                    # 而按下点所在窗口才是用户真要拖的那个。
                    hwnd = self._press_hwnd
                    probe = wa.window_info(hwnd) if (hwnd and wa.is_window(hwnd)) else {}
                    if (not probe or hwnd == self.root_hwnd
                            or probe.get("pid") == os.getpid()
                            or not probe.get("title")):
                        hwnd = wa.window_at_point(x, y)   # 兜底：落点最上层
                    root_logger.info(f"[拖放] 候选 hwnd={hwnd} root={self.root_hwnd} "
                                     f"pid={wa.get_pid(hwnd) if hwnd else None} self_pid={os.getpid()} "
                                     f"press=({px},{py}) press_hwnd={self._press_hwnd} drop=({x},{y})")
                    if hwnd and hwnd != self.root_hwnd and wa.get_pid(hwnd) != os.getpid():
                        # auto=True：拖入失败只更新状态栏，不弹模态框
                        # （模态框会堵住 Tk 主循环，连 /shot、/quit 都进不来）
                        self._attach_hwnd(hwnd, auto=True)
                else:
                    root_logger.info(f"[拖放] 未通过 dropped_in={dropped_in} "
                                     f"drop=({x},{y}) stage={r}")
            self._prev_down = down
        except Exception as e:
            root_logger.warning(f"[视觉工作台] 拖放检测异常：{e}")
        self.root.after(90, self._poll_drop)

    # ---------------- 舞台坐标系 ----------------

    def stage_bbox(self):
        """舞台在屏幕上的绝对矩形"""
        return self._stage_rect

    def target_rect(self):
        """截图与点击共用的基准：被嵌入软件自己的矩形（与舞台取交集）。

        用软件矩形而不是舞台矩形：软件保留自身尺寸时，舞台右下会有一块
        底色留白，把它算进坐标会让点击整体偏；用软件矩形则「截到什么就
        点在哪」，一个像素都不差。取交集是防止窗口溢出舞台后截到窗外的东西。
        """
        if self.attached:
            wr = wa.get_rect(self.attached["hwnd"])
            sl, st, sr, sb = self._stage_rect
            if wr and wr[2] - wr[0] > 20 and wr[3] - wr[1] > 20:
                return (max(wr[0], sl), max(wr[1], st),
                        min(wr[2], sr), min(wr[3], sb))
        return self._stage_rect

    def to_screen(self, x, y):
        l, t, r, b = self.target_rect()
        w, h = max(1, r - l), max(1, b - t)
        sx = int(l + w * float(x) / 1000.0)
        sy = int(t + h * float(y) / 1000.0)
        # 夹在基准矩形内，避免取整越界落到工作台自己的控件上
        return min(max(sx, l + 1), r - 1), min(max(sy, t + 1), b - 1)

    # ---------------- 截图 ----------------

    def _ensure_front(self):
        """截图与真实键鼠之前都要把工作台提到最前，否则抓到/点到的是遮挡物"""
        try:
            wa.bring_to_front(wa.root_of(self.root.winfo_id()))
            time.sleep(0.2)
        except Exception:
            pass

    def shot(self) -> dict:
        if not self.attached:
            return {"ok": False, "reason": "舞台里还没有软件，先启动或拖入一个"}
        os.makedirs(SHOT_DIR, exist_ok=True)
        self._ensure_front()
        path = os.path.join(SHOT_DIR,
                            f"ws_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png")
        # 与点击坐标严格同源的矩形：截到什么，点的就在哪
        img = wa.capture_rect(self.target_rect())
        if img is None:
            img = wa.capture_window(self.attached["hwnd"])
        if img is None:
            return {"ok": False, "reason": "截图失败"}
        img.save(path)
        dropped = prune_shots(SHOT_DIR)
        root_logger.info(f"[视觉工作台] 截图 {path} {img.size}"
                         + (f"，已清理 {dropped} 张旧图" if dropped else ""))
        return {"ok": True, "path": path, "size": list(img.size),
                "title": self.attached.get("title"), "hwnd": self.attached.get("hwnd"),
                "pruned": dropped}

    def shot_ui(self):
        res = self.shot()
        if res.get("ok"):
            self._set_status(f"已截图：{os.path.basename(res['path'])}")
        else:
            messagebox.showwarning("截图", res.get("reason", "失败"))

    # ---------------- 键鼠 ----------------

    def _guard(self):
        if not self.attached:
            return {"ok": False, "reason": "舞台里还没有软件"}
        # 真实键鼠打在前台窗口，所以动作前先保证工作台在最前
        self._ensure_front()
        return None

    def click(self, x, y, button="left", clicks=1) -> dict:
        bad = self._guard()
        if bad:
            return bad
        import pyautogui
        sx, sy = self.to_screen(x, y)
        wa.clip_cursor(self.target_rect())
        try:
            pyautogui.click(sx, sy, clicks=int(clicks), button=str(button), interval=0.08)
        finally:
            wa.release_cursor()
        root_logger.info(f"[视觉工作台] 点击 ({x},{y}) -> 屏幕({sx},{sy})")
        return {"ok": True, "screen": [sx, sy]}

    def drag(self, x1, y1, x2, y2, duration=0.4) -> dict:
        bad = self._guard()
        if bad:
            return bad
        import pyautogui
        s1 = self.to_screen(x1, y1)
        s2 = self.to_screen(x2, y2)
        wa.clip_cursor(self.target_rect())
        try:
            steps = max(12, min(60, int(abs(s2[0] - s1[0]) + abs(s2[1] - s1[1])) // 25))
            wa.drag_to(s1[0], s1[1], s2[0], s2[1], steps=steps,
                       step_delay=max(0.008, min(0.05, float(duration) / steps)))
        finally:
            wa.release_cursor()
        root_logger.info(f"[视觉工作台] 拖拽 ({x1},{y1})->({x2},{y2})")
        return {"ok": True, "from": list(s1), "to": list(s2)}

    def type_text(self, text) -> dict:
        bad = self._guard()
        if bad:
            return bad
        import pyautogui
        text = str(text)[:2000]
        if all(ord(c) < 128 for c in text):
            pyautogui.write(text, interval=0.02)
        else:
            if not _set_clipboard_text(text):
                return {"ok": False, "reason": "中文输入需要剪贴板，写入失败"}
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.2)
        return {"ok": True, "chars": len(text)}

    def press_key(self, key) -> dict:
        bad = self._guard()
        if bad:
            return bad
        import pyautogui
        key = str(key).strip().lower()
        if "+" in key:
            pyautogui.hotkey(*[k.strip() for k in key.split("+")])
        else:
            pyautogui.press(key)
        return {"ok": True, "key": key}

    def scroll(self, delta) -> dict:
        bad = self._guard()
        if bad:
            return bad
        import pyautogui
        l, t, r, b = self.target_rect()
        cx, cy = (l + r) // 2, (t + b) // 2
        pyautogui.moveTo(cx, cy)
        pyautogui.scroll(int(float(delta)))
        return {"ok": True, "delta": delta}

    # ---------------- 状态 ----------------

    def state(self) -> dict:
        # 被嵌入的进程可能自己退出（实测 Tk 窗口跨进程 SetParent 后进程会消失）。
        # 不校验存活的话，/state 会一直报一个已经不存在的 hwnd，让模型以为舞台里有东西。
        if self.attached and not wa.is_window(self.attached.get("hwnd")):
            gone = self.attached
            self.attached = None
            root_logger.warning(f"[视觉工作台] 已附着的窗口不见了 hwnd={gone.get('hwnd')} "
                                f"title={gone.get('title')!r}，已清空附着状态")
            self._set_status(f"「{gone.get('title')}」的窗口已经消失，舞台已空")
            self._ui(lambda: self.stage_hint.place(relx=0.5, rely=0.5, anchor="center"))
        a = self.attached or {}
        return {
            "ok": True,
            "attached": bool(self.attached),
            "hwnd": a.get("hwnd"),
            "title": a.get("title"),
            "cls": a.get("cls"),
            "stage": list(self._stage_rect),
            "port": self.port,
            "software": [i.get("name") for i in self.registry.get("items", [])],
        }

    def _set_status(self, text):
        self._ui(self._apply_status, text)

    def _apply_status(self, text):
        try:
            self.status.configure(text=text)
        except Exception:
            pass

    # ---------------- HTTP ----------------

    def _start_http(self):
        global _WS
        _WS = self
        handler = _make_handler()
        for port in range(START_PORT, MAX_PORT + 1):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
                self.httpd.daemon_threads = True
                self.port = port
                break
            except OSError:
                continue
        if not self.httpd:
            root_logger.warning("[视觉工作台] 端口全被占用，模型侧接口不可用")
            return
        os.makedirs(os.path.dirname(PORT_FILE), exist_ok=True)
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(self.port))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        root_logger.info(f"[视觉工作台] 控制接口 http://127.0.0.1:{self.port}")
        self._set_status(f"控制接口 http://127.0.0.1:{self.port}")

    def _cleanup_port_file(self):
        try:
            if os.path.exists(PORT_FILE):
                os.remove(PORT_FILE)
        except Exception:
            pass

    # ---------------- 退出 ----------------

    def on_close(self):
        """任意线程可调；真正的关闭在主线程做"""
        if self._closing:
            return
        self._closing = True
        self._ui(self._do_close)

    def _do_close(self):
        try:
            self.detach()          # 关键：先解除嵌入，否则宿主销毁会挂死
        except Exception as e:
            root_logger.warning(f"[视觉工作台] 关闭前解嵌失败：{e}")
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            pass
        self._cleanup_port_file()
        self.root.after(1500, lambda: os._exit(0))   # 万一还挂住，兜底强退
        try:
            self.root.destroy()
        except Exception:
            os._exit(0)

    def run(self):
        self.root.mainloop()


_WS = None


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass                     # 不刷屏

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self):
            ws = _WS
            p = self.path.split("?")[0]
            if p == "/state":
                return self._send(ws.state())
            if p == "/software":
                return self._send({"ok": True, "items": ws.registry.get("items", [])})
            if p == "/windows":
                return self._send({"ok": True,
                                   "windows": wa.list_windows(exclude_pids=[os.getpid()])})
            return self._send({"ok": False, "reason": "unknown path"}, 404)

        def do_POST(self):
            ws = _WS
            p = self.path.split("?")[0]
            d = self._body()
            try:
                if p == "/launch":
                    name = d.get("name") or ""
                    item = next((i for i in ws.registry.get("items", []) if i.get("name") == name), None)
                    if not item and name:
                        item = next((i for i in ws.registry.get("items", [])
                                     if name in i.get("name", "")), None)
                    if not item:
                        return self._send({"ok": False, "reason": f"软件地址管理器里没有「{name}」"})
                    if ws.attached:
                        ws.detach()
                    res = wa.launch(item.get("target", ""), item.get("args", ""))
                    if res.get("ok"):
                        hwnd = None
                        deadline = time.time() + 15
                        while time.time() < deadline and hwnd is None:
                            if item.get("title_hint"):
                                hwnd = wa.find_window(title_has=item["title_hint"])
                            if hwnd is None and item.get("cls_hint"):
                                hwnd = wa.find_window(cls_has=item["cls_hint"])
                            time.sleep(0.4)
                        if hwnd:
                            res["attached"] = ws._attach_hwnd(hwnd, auto=True)
                            res["hwnd"] = hwnd
                        else:
                            res["attached"] = False
                            res["reason"] = "窗口没等到，可用 /windows + /attach 手动嵌入"
                    return self._send(res)
                if p == "/attach":
                    hwnd = d.get("hwnd")
                    if not hwnd and d.get("title"):
                        hwnd = wa.wait_window(title_has=d["title"], timeout=8)
                    if not hwnd:
                        return self._send({"ok": False, "reason": "没找到窗口"})
                    return self._send({"ok": ws._attach_hwnd(int(hwnd))})
                if p == "/detach":
                    return self._send(ws.detach())
                if p == "/shot":
                    return self._send(ws.shot())
                if p == "/click":
                    return self._send(ws.click(d.get("x", 500), d.get("y", 500),
                                               d.get("button", "left"), d.get("clicks", 1)))
                if p == "/drag":
                    return self._send(ws.drag(d.get("x1", 100), d.get("y1", 500),
                                              d.get("x2", 900), d.get("y2", 500),
                                              d.get("duration", 0.4)))
                if p == "/type":
                    return self._send(ws.type_text(d.get("text", "")))
                if p == "/key":
                    return self._send(ws.press_key(d.get("key", "enter")))
                if p == "/scroll":
                    return self._send(ws.scroll(d.get("delta", 2)))
                if p == "/quit":
                    self._send({"ok": True})
                    ws.on_close()
                    return
            except Exception as e:
                root_logger.warning(f"[视觉工作台] 接口异常 {p}: {e}")
                return self._send({"ok": False, "reason": str(e)})
            return self._send({"ok": False, "reason": "unknown path"}, 404)

    return Handler


def main():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    root_logger.info("[视觉工作台] 启动")
    Workspace().run()


if __name__ == "__main__":
    main()
