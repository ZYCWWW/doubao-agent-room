"""
Win32 窗口层：枚举 / 嵌入 / 移动 / 单窗口截图 / 光标限制

供「视觉工作台」（services/vision_workspace.py）使用：把用户桌面上的软件
窗口用 SetParent 嵌进自绘窗口，之后只在该窗口内截图并做键鼠操作。

纯 ctypes 实现，不依赖 pywin32。

实测结论（Win11 / 2026-11）：
- Win32 窗口与 UWP 打包应用（Win11 记事本 class='Notepad'、画图）都能嵌入并正常渲染
- PrintWindow(PW_RENDERFULLCONTENT) 对打包应用同样有效，被其他窗口遮挡也抓得到
- 跨进程嵌入后可能出现重影，attach 之后需要 SetWindowPos(FRAMECHANGED) + InvalidateRect
- 宿主窗口销毁前必须先 detach，否则进程会挂死
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from core.logger import root_logger

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# Win32 常量
GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
GA_ROOT = 2
PW_RENDERFULLCONTENT = 0x00000002
SW_SHOW = 5
SW_RESTORE = 9
SWP_FRAMECHANGED = 0x0020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
VK_LBUTTON = 0x01

_GetWindowLong = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_SetWindowLong = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW


def dpi_aware():
    """声明进程 DPI 感知，避免截图像素与鼠标坐标错位（幂等，可重复调用）"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
        return True
    except Exception:
        try:
            user32.SetProcessDPIAware()
            return True
        except Exception:
            return False


# 系统外壳窗口：桌面、任务栏、开始菜单、任务视图、小部件。
# 这些窗口一旦被 SetParent 嵌进舞台，桌面/任务栏就会整块从屏幕上消失
# （实测：嵌入 Progman 后屏幕全黑，只剩任务栏，看起来像系统崩了）。
_SHELL_CLASSES = {
    "progman",                 # 桌面
    "workerw",                 # 桌面壁纸层
    "shell_traywnd",           # 主任务栏
    "shell_secondarytraywnd",  # 副屏任务栏
    "windowsdashboard",        # 小部件面板
    "multitaskingviewframe",   # 任务视图
    "dv2controlhost",          # 开始菜单宿主
}


def is_shell_window(hwnd) -> bool:
    """桌面/任务栏/开始菜单等系统外壳窗口，不能作为嵌入目标"""
    cls = (get_class(hwnd) or "").strip().lower()
    if not cls:
        return False
    return cls in _SHELL_CLASSES or cls.startswith("shell_")


# ---------------- 窗口枚举 ----------------

def is_window(hwnd) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(int(hwnd)))


def get_rect(hwnd):
    """窗口矩形（屏幕绝对坐标）-> (left, top, right, bottom)"""
    r = wt.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def client_rect_screen(hwnd):
    """窗口客户区的屏幕矩形。

    截舞台、点舞台都以它为准：边框/标题栏不算，且子窗口溢出客户区的部分
    本来就被系统裁掉不可见，按客户区取就正好是「用户和模型看到的那一块」。
    """
    rc = wt.RECT()
    if not user32.GetClientRect(int(hwnd), ctypes.byref(rc)):
        return None
    pt = wt.POINT(0, 0)
    if not user32.ClientToScreen(int(hwnd), ctypes.byref(pt)):
        return None
    return (int(pt.x), int(pt.y), int(pt.x) + int(rc.right), int(pt.y) + int(rc.bottom))


def get_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(int(hwnd))
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(int(hwnd), buf, n + 1)
    return buf.value


def get_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(int(hwnd), buf, 256)
    return buf.value


def get_pid(hwnd) -> int:
    p = wt.DWORD()
    user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(p))
    return int(p.value)


def window_info(hwnd) -> dict:
    rect = get_rect(hwnd)
    w = (rect[2] - rect[0]) if rect else 0
    h = (rect[3] - rect[1]) if rect else 0
    return {
        "hwnd": int(hwnd),
        "title": get_title(hwnd),
        "cls": get_class(hwnd),
        "pid": get_pid(hwnd),
        "rect": rect,
        "width": w,
        "height": h,
        "visible": bool(user32.IsWindowVisible(int(hwnd))),
    }


def list_windows(visible_only: bool = True, min_w: int = 60, min_h: int = 40,
                 exclude_pids=()) -> list:
    """枚举顶层窗口：[{hwnd,title,cls,pid,rect,width,height,visible}]"""
    out = []
    skip = set(exclude_pids or ())

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lp):
        h = int(hwnd)
        if user32.GetAncestor(h, GA_ROOT) != h:      # 只要顶层
            return True
        if visible_only and not user32.IsWindowVisible(h):
            return True
        if user32.GetWindowTextLengthW(h) == 0:      # 无标题的多是辅助窗口
            return True
        pid = get_pid(h)
        if pid in skip:
            return True
        rect = get_rect(h)
        if not rect:
            return True
        if (rect[2] - rect[0]) < min_w or (rect[3] - rect[1]) < min_h:
            return True
        if is_shell_window(h):       # 桌面/任务栏不能出现在"可嵌入"列表里
            return True
        out.append({
            "hwnd": h,
            "title": get_title(h),
            "cls": get_class(h),
            "pid": pid,
            "rect": rect,
            "width": rect[2] - rect[0],
            "height": rect[3] - rect[1],
            "visible": True,
        })
        return True

    user32.EnumWindows(_cb, 0)
    return out


def find_window(title_has=None, cls_has=None, pid=None, visible_only: bool = True):
    """按标题/类名/pid 找第一个匹配的顶层窗口，返回 hwnd 或 None"""
    for w in list_windows(visible_only=visible_only):
        if pid is not None and w["pid"] != pid:
            continue
        if title_has and title_has.lower() not in (w["title"] or "").lower():
            continue
        if cls_has and cls_has.lower() not in (w["cls"] or "").lower():
            continue
        return w["hwnd"]
    return None


def wait_window(title_has=None, cls_has=None, pid=None, timeout: float = 12.0,
                interval: float = 0.3):
    """轮询等窗口出现。固定 sleep 不可靠（打包应用冷启动可能 5s+）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window(title_has=title_has, cls_has=cls_has, pid=pid)
        if hwnd:
            return hwnd
        time.sleep(interval)
    return None


def window_at_point(x: int, y: int):
    """屏幕坐标上最上层的顶层窗口句柄"""
    pt = wt.POINT(int(x), int(y))
    h = user32.WindowFromPoint(pt)
    if not h:
        return None
    root = user32.GetAncestor(h, GA_ROOT)
    return int(root) if root else int(h)


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def left_button_down() -> bool:
    """左键当前是否按下（拖放检测用，异步键盘状态不受窗口焦点影响）"""
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


# ---------------- 嵌入 / 解除 ----------------

def attach(hwnd, host_hwnd, x=0, y=0, w=None, h=None) -> dict:
    """把外部窗口嵌进 host 的客户区。

    返回 {ok, hwnd, title, cls, pid, old_style, old_rect}，ok=False 时带 reason。
    """
    if not is_window(hwnd):
        return {"ok": False, "reason": "目标窗口不存在"}
    if is_shell_window(hwnd):
        # 硬防线：桌面/任务栏若被嵌走，屏幕会整块变黑（模型可能按
        # "Program Manager" 这类标题误命中，或从桌面空白处拖入）
        return {"ok": False, "reason": "这是系统外壳窗口（桌面/任务栏），不能嵌入舞台"}
    host_hwnd = int(host_hwnd)
    if not is_window(host_hwnd):
        return {"ok": False, "reason": "宿主窗口不存在"}

    info = window_info(hwnd)
    old_style = int(_GetWindowLong(int(hwnd), GWL_STYLE))
    old_rect = info["rect"]

    user32.ShowWindow(int(hwnd), SW_RESTORE)          # 最小化状态先还原

    if w is None or h is None:
        rect = get_rect(host_hwnd) or (0, 0, 800, 600)
        w = w or max(200, rect[2] - rect[0] - x - 2)
        h = h or max(150, rect[3] - rect[1] - y - 2)

    # 先在桌面态把尺寸定好、让应用自己重排布局，再嵌进去。
    # 反过来（先 SetParent 再 MoveWindow）实测会让 UWP 应用布局错位：
    # 左边缘内容被裁、右侧留黑条。
    if old_rect and (abs((old_rect[2] - old_rect[0]) - w) > 2 or
                     abs((old_rect[3] - old_rect[1]) - h) > 2):
        user32.MoveWindow(int(hwnd), old_rect[0], old_rect[1], int(w), int(h), True)
        time.sleep(0.45)

    prev_parent = user32.SetParent(int(hwnd), host_hwnd)
    if prev_parent == 0 and user32.GetParent(int(hwnd)) != host_hwnd:
        return {"ok": False, "reason": "SetParent 失败（该窗口可能不允许被重设父窗口）"}

    # 去掉 POPUP/CAPTION/THICKFRAME：捕进宿主后这些系统边框会让客户区相对
    # 窗口矩形整体偏移，实测表现为内容左移约 47px（左边缘按钮被裁）。
    new_style = (old_style | WS_CHILD) & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME)
    _SetWindowLong(int(hwnd), GWL_STYLE, new_style)

    user32.MoveWindow(int(hwnd), int(x), int(y), int(w), int(h), True)
    # 跨进程嵌入后常有重影：改完样式强制重排 + 重绘
    user32.SetWindowPos(int(hwnd), 0, int(x), int(y), int(w), int(h),
                        SWP_FRAMECHANGED | SWP_NOZORDER | SWP_NOACTIVATE)
    try:
        user32.InvalidateRect(int(hwnd), None, True)
        user32.UpdateWindow(int(hwnd))
    except Exception:
        pass
    user32.ShowWindow(int(hwnd), SW_SHOW)
    send_size(hwnd, w, h)

    root_logger.info(f"[视觉工作台] 已嵌入 hwnd=0x{int(hwnd):X} title={info['title']!r} cls={info['cls']}")
    return {"ok": True, "hwnd": int(hwnd), "title": info["title"], "cls": info["cls"],
            "pid": info["pid"], "old_style": old_style, "old_rect": old_rect,
            "host": host_hwnd, "size": [int(w), int(h)], "offset": [int(x), int(y)]}


def detach(hwnd, old_style=None, old_rect=None) -> dict:
    """解除嵌入：还原父窗口、样式与位置。关闭宿主前必须调用"""
    h = int(hwnd)
    if not is_window(h):
        return {"ok": True, "reason": "窗口已不存在"}
    try:
        user32.SetParent(h, 0)
    except Exception as e:
        return {"ok": False, "reason": f"SetParent 还原失败：{e}"}
    if old_style:
        _SetWindowLong(h, GWL_STYLE, int(old_style))
    if old_rect and len(old_rect) == 4:
        l, t, r, b = [int(v) for v in old_rect]
        user32.MoveWindow(h, l, t, max(100, r - l), max(80, b - t), True)
    user32.SetWindowPos(h, 0, 0, 0, 0, 0,
                        SWP_FRAMECHANGED | SWP_NOZORDER | SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    user32.ShowWindow(h, SW_SHOW)
    root_logger.info(f"[视觉工作台] 已解除嵌入 hwnd=0x{h:X}")
    return {"ok": True}


def resize_in_host(hwnd, x, y, w, h):
    """宿主内重新摆放已嵌入的窗口"""
    user32.MoveWindow(int(hwnd), int(x), int(y), int(w), int(h), True)
    send_size(hwnd, w, h)


# ---------------- 前台与遮挡 ----------------

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_SHOWWINDOW = 0x0040


def root_of(hwnd) -> int:
    """Tk 的 winfo_id 是内层子窗口，抓图/置前都要用真正的顶层窗口"""
    try:
        r = int(user32.GetAncestor(int(hwnd), GA_ROOT) or 0)
        return r or int(hwnd)
    except Exception:
        return int(hwnd)


def bring_to_front(hwnd) -> bool:
    """把窗口提到最前。

    舞台里嵌的是别的进程的窗口，工作台一旦被别的应用盖住，屏幕抓图抓到的是
    遮挡物、真实点击也会打到遮挡物，所以截图和动作前都必须先置前。
    """
    h = int(hwnd)
    if not is_window(h):
        return False
    try:
        user32.ShowWindow(h, SW_RESTORE)
        user32.SetForegroundWindow(h)
        user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        time.sleep(0.12)
        user32.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        return True
    except Exception:
        return False


# ---------------- 键鼠注入 ----------------

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


def mouse_move_abs(x, y) -> None:
    """用 mouse_event 注入绝对移动。

    只调 SetCursorPos 时系统不产生 pointer 消息，UWP 应用（如画图）收不到拖动
    过程，画布上只会留下一个点。
    """
    sw = max(1, int(user32.GetSystemMetrics(0)) - 1)
    sh = max(1, int(user32.GetSystemMetrics(1)) - 1)
    nx = int(int(x) * 65535 / sw)
    ny = int(int(y) * 65535 / sh)
    user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny, 0, 0)


def mouse_down() -> None:
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def mouse_up() -> None:
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def drag_to(x1, y1, x2, y2, steps=26, step_delay=0.02) -> dict:
    """按步注入的拖动，保证目标应用收到完整 pointermove 轨迹"""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    user32.SetCursorPos(x1, y1)
    mouse_move_abs(x1, y1)
    time.sleep(0.06)
    mouse_down()
    time.sleep(0.08)
    for i in range(1, steps + 1):
        mx = int(round(x1 + (x2 - x1) * i / steps))
        my = int(round(y1 + (y2 - y1) * i / steps))
        mouse_move_abs(mx, my)
        time.sleep(step_delay)
    time.sleep(0.08)
    mouse_up()
    return {"from": [x1, y1], "to": [x2, y2], "steps": steps}


WM_SIZE = 0x0005


def send_size(hwnd, w, h):
    """显式补一个 WM_SIZE：跨进程嵌入后有些应用（含 UWP 画图）不会自己重排内部布局"""
    try:
        user32.SendMessageW(int(hwnd), WM_SIZE, 0, (int(h) << 16) | (int(w) & 0xFFFF))
        return True
    except Exception:
        return False


# ---------------- 截图 ----------------

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def capture_window(hwnd):
    """按窗口抓图（PrintWindow 走 PW_RENDERFULLCONTENT），被遮挡也能抓到。

    :return: PIL.Image 或 None
    """
    from PIL import Image
    h = int(hwnd)
    if not is_window(h):
        return None
    rect = get_rect(h)
    if not rect:
        return None
    w, hh = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or hh <= 0:
        return None
    hdc = user32.GetWindowDC(h)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, hh)
    gdi32.SelectObject(mdc, bmp)
    try:
        user32.PrintWindow(h, mdc, PW_RENDERFULLCONTENT)
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -hh            # 负值 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = (ctypes.c_char * (w * hh * 4))()
        got = gdi32.GetDIBits(mdc, bmp, 0, hh, buf, ctypes.byref(bi), 0)
        if not got:
            return None
        return Image.frombuffer("RGB", (w, hh), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(h, hdc)


def capture_rect(bbox):
    """按屏幕矩形抓图（bbox=(l,t,r,b)），使用当前进程的 DPI 坐标"""
    from PIL import ImageGrab
    l, t, r, b = [int(v) for v in bbox]
    if r - l <= 0 or b - t <= 0:
        return None
    return ImageGrab.grab(bbox=(l, t, r, b))


# ---------------- 光标限制 ----------------

def clip_cursor(bbox):
    """把鼠标限制在矩形内，防误触舞台外的窗口"""
    l, t, r, b = [int(v) for v in bbox]
    rect = wt.RECT(l, t, r, b)
    return bool(user32.ClipCursor(ctypes.byref(rect)))


def release_cursor():
    user32.ClipCursor(None)


# ---------------- 启动软件 ----------------

def launch(target: str, args: str = "", cwd: str = None):
    """启动软件。

    支持三类 target：
    - 可执行路径或系统命令（notepad.exe）
    - shell: URI（UWP 打包应用，如画图 shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App）
    - 带参数的路径（args 单独给）
    - .py 脚本（自建软件，如计算器 apps/calculator.py）：用项目自带的 pythonw
      拉起，相对路径按项目根解析，这样登记表里不用写死绝对路径
    """
    target = (target or "").strip()
    if not target:
        return {"ok": False, "reason": "未给启动目标"}
    try:
        if target.lower().startswith("shell:"):
            proc = subprocess.Popen(["explorer.exe", target])
        elif target.lower().endswith(".py"):
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            script = target if os.path.isabs(target) else os.path.join(root, target)
            gui = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
            exe = gui if os.path.exists(gui) else sys.executable
            proc = subprocess.Popen([exe, script] + ([a for a in args.split()] if args else []),
                                    cwd=cwd or root)
        else:
            cmd = [target] + ([a for a in args.split()] if args else [])
            proc = subprocess.Popen(cmd, cwd=cwd)
    except Exception as e:
        root_logger.warning(f"[视觉工作台] 启动失败 {target}: {e}")
        return {"ok": False, "reason": f"启动失败：{e}"}
    root_logger.info(f"[视觉工作台] 已启动 {target} pid={getattr(proc, 'pid', '?')}")
    return {"ok": True, "pid": getattr(proc, "pid", None), "target": target}


def close_window(hwnd) -> bool:
    """给自己的窗口发关闭（WM_CLOSE），软件自己决定保存与否"""
    try:
        return bool(user32.PostMessageW(int(hwnd), 0x0010, 0, 0))
    except Exception:
        return False
