"""能被视觉工作台嵌进舞台的计算器。

系统自带的计算器是 ApplicationFrameHost 托管的 UWP 窗口，SetParent 会被拒；
这里做一个自建窗口顶上：原生 Win32 窗口 + GDI 自绘，窗口类名 AgentCalcWnd、
标题「计算器·内置」，工作台按标题就能定位。

为什么不用 tkinter：Tk 的顶层窗口被跨进程 SetParent 之后，进程会直接退出
（实测拖进舞台后窗口消失、/state 只剩一个指向死 hwnd 的陈旧记录）。原生窗口
在 WS_CHILD 化之后照常响应 WM_PAINT / WM_LBUTTONDOWN，是能稳定被嵌入的形态。

启动：pythonw apps/calculator.py（软件地址管理器里的「计算器」也拉起它）

求值不用 eval：表达式过 ast 白名单，只放行数字和加减乘除，别的节点一律拒。
"""

import ast
import ctypes
from ctypes import wintypes

# ---------------- Win32 基础 ----------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_GETMINMAXINFO = 0x0024
WM_CTLCOLORSTATIC = 0x0138

CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
CW_USEDEFAULT = 0x80000000
SW_SHOW = 5
DT_RIGHT, DT_CENTER, DT_VCENTER, DT_SINGLELINE = 0x0002, 0x0001, 0x0004, 0x0020
TRANSPARENT = 1
DEFAULT_CHARSET = 1
CLEARTYPE_QUALITY = 5
DEFAULT_PITCH = 0
VK_RETURN = 13
VK_ESCAPE = 27
VK_BACK = 8

TITLE = "计算器·内置"
CLASS_NAME = "AgentCalcWnd"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.SetCapture.restype = wintypes.HWND
user32.ReleaseCapture.argtypes = []
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.PostQuitMessage.argtypes = [ctypes.c_int]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.CreateFontW.restype = wintypes.HFONT
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wintypes.RECT), wintypes.UINT]
# ---------------- 配色 ----------------
COL_BG = "#1b1b1f"
COL_PANEL = "#232329"
COL_NUM = "#2c2c33"
COL_NUM_HOT = "#3a3a42"
COL_OP = "#3c3c46"
COL_OP_HOT = "#4a4a56"
COL_EQ = "#4c8bf5"
COL_EQ_HOT = "#6aa1ff"
COL_CLEAR = "#5a3b3b"
COL_CLEAR_HOT = "#6d4747"
COL_TEXT = "#f2f2f5"
COL_DIM = "#8d8d99"
COL_WARN = "#ff9b8a"


def rgb(hexstr: str) -> int:
    h = hexstr.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


# ---------------- 求值 ----------------
class _Evaluator(ast.NodeVisitor):
    """只算四则运算，别的 AST 节点一律拒绝。"""

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("只支持数字")
        return v

    def visit_UnaryOp(self, node):
        v = self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
        raise ValueError("不支持的运算符")

    def visit_BinOp(self, node):
        a = self.visit(node.left)
        b = self.visit(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            if b == 0:
                raise ZeroDivisionError("除数为 0")
            return a / b
        raise ValueError("不支持的运算符")

    def generic_visit(self, node):
        raise ValueError("表达式里出现了不支持的语法")


def evaluate(text: str) -> float:
    expr = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("＋", "+")
    node = ast.parse(expr, mode="eval")
    return float(_Evaluator().visit(node))


def fmt(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return "错误"
    if value == 0:
        return "0"
    if abs(value) >= 1e15:
        return "%.6e" % value
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return ("%.10f" % value).rstrip("0").rstrip(".")


# ---------------- 界面 ----------------
ROWS = [
    [("C", "clear"), ("←", "back"), ("%", "pct"), ("÷", "op")],
    [("7", "num"), ("8", "num"), ("9", "num"), ("×", "op")],
    [("4", "num"), ("5", "num"), ("6", "num"), ("−", "op")],
    [("1", "num"), ("2", "num"), ("3", "num"), ("＋", "op")],
    [("±", "neg"), ("0", "num"), (".", "num"), ("=", "eq")],
]
OPS = ("＋", "−", "×", "÷")

_CALC = None


class Calculator:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.expr = ""
        self.result = None
        self.history = ""
        self.notice = ""
        self.down_idx = -1
        self.hot_idx = -1
        self.cw = 380
        self.ch = 540
        self._brushes = {k: gdi32.CreateSolidBrush(rgb(v)) for k, v in (
            ("bg", COL_BG), ("panel", COL_PANEL),
            ("num", COL_NUM), ("num_hot", COL_NUM_HOT),
            ("op", COL_OP), ("op_hot", COL_OP_HOT),
            ("eq", COL_EQ), ("eq_hot", COL_EQ_HOT),
            ("clear", COL_CLEAR), ("clear_hot", COL_CLEAR_HOT),
        )}
        self._fonts = {}
        for size in (30, 24, 19, 15):
            self._fonts[f"main{size}"] = self._font(-int(size * 1.34), "Consolas", 700)
        self._fonts["hist"] = self._font(-16, "Microsoft YaHei UI", 400)
        self._fonts["btn"] = self._font(-22, "Microsoft YaHei UI", 600)
        self._fonts["btn_s"] = self._font(-18, "Microsoft YaHei UI", 600)
        self._fonts["warn"] = self._font(-26, "Microsoft YaHei UI", 700)

    @staticmethod
    def _font(height, face, weight):
        return gdi32.CreateFontW(height, 0, 0, 0, weight, 0, 0, 0,
                                 DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                                 DEFAULT_PITCH, face)

    # ---------- 布局 ----------
    def layout(self):
        """返回 (显示区 rect, [(rect, label, kind) ...])，坐标是客户区像素。"""
        pad = 14
        w, h = self.cw, self.ch
        disp = (pad, pad, w - pad, pad + 112)
        gx0, gy0 = pad, disp[3] + 10
        gx1, gy1 = w - pad, h - pad
        cellw = (gx1 - gx0) / 4.0
        cellh = (gy1 - gy0) / 5.0
        btns = []
        for r, row in enumerate(ROWS):
            for c, (label, kind) in enumerate(row):
                x0 = int(gx0 + c * cellw) + 4
                y0 = int(gy0 + r * cellh) + 4
                x1 = int(gx0 + (c + 1) * cellw) - 4
                y1 = int(gy0 + (r + 1) * cellh) - 4
                btns.append(((x0, y0, x1, y1), label, kind))
        return disp, btns

    def hit(self, x, y):
        _disp, btns = self.layout()
        for i, (r, _l, _k) in enumerate(btns):
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return i
        return -1

    # ---------- 绘制 ----------
    def paint(self, hdc):
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, self.cw, self.ch)
        old = gdi32.SelectObject(mem, bmp)
        try:
            self._draw(mem)
            gdi32.BitBlt(hdc, 0, 0, self.cw, self.ch, mem, 0, 0, 0x00CC0020)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)

    def _fill(self, dc, rect, key):
        r = wintypes.RECT(*[int(v) for v in rect])
        user32.FillRect(dc, ctypes.byref(r), self._brushes[key])

    def _text(self, dc, text, rect, font, color, flags):
        gdi32.SelectObject(dc, self._fonts[font])
        gdi32.SetTextColor(dc, rgb(color))
        gdi32.SetBkMode(dc, TRANSPARENT)
        r = wintypes.RECT(*[int(v) for v in rect])
        user32.DrawTextW(dc, text, -1, ctypes.byref(r), flags)

    def _draw(self, dc):
        self._fill(dc, (0, 0, self.cw, self.ch), "bg")
        disp, btns = self.layout()
        self._fill(dc, disp, "panel")

        hist_rect = (disp[0] + 14, disp[1] + 12, disp[2] - 14, disp[1] + 40)
        self._text(dc, self.history, hist_rect, "hist", COL_DIM,
                   DT_RIGHT | DT_VCENTER | DT_SINGLELINE)

        main_rect = (disp[0] + 14, disp[1] + 44, disp[2] - 14, disp[3] - 10)
        if self.notice:
            self._text(dc, self.notice, main_rect, "warn", COL_WARN,
                       DT_RIGHT | DT_VCENTER | DT_SINGLELINE)
        else:
            if self.result is not None:
                text = fmt(self.result)
            else:
                text = self.expr or "0"
            n = len(text)
            size = 30 if n <= 12 else 24 if n <= 16 else 19 if n <= 22 else 15
            self._text(dc, text, main_rect, f"main{size}", COL_TEXT,
                       DT_RIGHT | DT_VCENTER | DT_SINGLELINE)

        for i, (r, label, kind) in enumerate(btns):
            hot = (i == self.hot_idx) or (i == self.down_idx)
            if kind == "eq":
                key = "eq_hot" if hot else "eq"
            elif kind == "clear":
                key = "clear_hot" if hot else "clear"
            elif kind == "op" or kind == "back":
                key = "op_hot" if hot else "op"
            else:
                key = "num_hot" if hot else "num"
            self._fill(dc, r, key)
            fg = "#ffffff" if kind == "eq" else ("#ffd9d9" if kind == "clear" else COL_TEXT)
            font = "btn_s" if kind in ("clear", "back") else "btn"
            self._text(dc, label, r, font, fg, DT_CENTER | DT_VCENTER | DT_SINGLELINE)

    # ---------- 输入 ----------
    def on_down(self, x, y):
        self.down_idx = self.hit(x, y)
        self.hot_idx = self.down_idx
        self.invalidate()

    def on_move(self, x, y, pressed):
        idx = self.hit(x, y)
        if idx != self.hot_idx:
            self.hot_idx = idx if pressed else -1
            self.invalidate()

    def on_up(self, x, y):
        idx = self.hit(x, y)
        if idx >= 0 and idx == self.down_idx:
            _disp, btns = self.layout()
            label, kind = btns[idx][1], btns[idx][2]
            self.press(label, kind)
        self.down_idx = -1
        self.hot_idx = -1
        self.invalidate()

    def invalidate(self):
        user32.InvalidateRect(self.hwnd, None, False)

    def press(self, label, kind):
        self.notice = ""
        if kind == "clear":
            self.expr, self.result, self.history = "", None, ""
        elif kind == "back":
            if self.result is not None:
                self.expr, self.result = "", None
            else:
                self.expr = self.expr[:-1]
        elif kind == "num":
            if self.result is not None:
                self.expr, self.result, self.history = "", None, ""
            self._push_digit(label)
        elif kind == "neg":
            self._negate()
        elif kind == "pct":
            self._apply_pct()
        elif kind == "op":
            self._push_operator(label)
        elif kind == "eq":
            self._commit()

    def key(self, ch):
        if ch in "0123456789.":
            self.press(ch, "num")
        elif ch in ("+", "＋"):
            self.press("＋", "op")
        elif ch == "-":
            self.press("−", "op")
        elif ch == "*":
            self.press("×", "op")
        elif ch == "/":
            self.press("÷", "op")
        elif ch == "%":
            self.press("%", "pct")
        elif ch in ("\r", "\n"):
            self.press("=", "eq")
        elif ch == "\x08":
            self.press("←", "back")
        elif ch == "\x1b":
            self.press("C", "clear")

    def _operand_start(self):
        idx = -1
        for op in OPS:
            idx = max(idx, self.expr.rfind(op))
        return idx + 1

    def _push_digit(self, ch):
        if ch == ".":
            tail = self.expr[self._operand_start():]
            if "." in tail:
                return
            self.expr += "0." if tail == "" else "."
            return
        self.expr += ch

    def _push_operator(self, op):
        if not self.expr:
            self.expr = "0"
        if self.expr.endswith(OPS):
            self.expr = self.expr[:-1] + op
        else:
            self.expr += op
        self.result = None

    def _apply_pct(self):
        """200＋10% → 200＋20；200×10% → 200×0.1；单个数按 % 就是除以 100。"""
        if not self.expr:
            self.expr = "0"
        start = self._operand_start()
        try:
            if start == 0:
                self.expr = fmt(evaluate(self.expr) / 100.0)
            else:
                op = self.expr[start - 1]
                base = evaluate(self.expr[:start - 1])
                cur = evaluate(self.expr[start:] or "0")
                v = cur / 100.0 * base if op in ("＋", "−") else cur / 100.0
                self.expr = self.expr[:start - 1] + op + fmt(v)
        except Exception:
            self.notice = "这段算不了"
            return
        self.result = None

    def _negate(self):
        if self.result is not None:
            self.result = -self.result
            return
        start = self._operand_start()
        tail = self.expr[start:]
        if tail.startswith("-"):
            self.expr = self.expr[:start] + tail[1:]
        elif tail:
            self.expr = self.expr[:start] + "-" + tail
        else:
            self.expr += "-"

    def _commit(self):
        if not self.expr:
            return
        self.history = self.expr + " ="
        try:
            value = evaluate(self.expr)
        except ZeroDivisionError:
            self.notice = "除数为 0"
        except Exception:
            self.notice = "表达式有误"
        else:
            self.result = value
        self.expr = ""


def _wndproc(hwnd, msg, wparam, lparam):
    global _CALC
    c = _CALC
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            if c:
                rc = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rc))
                c.cw, c.ch = rc.right, rc.bottom
                c.paint(hdc)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_GETMINMAXINFO:
        mmi = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
        mmi.ptMinTrackSize.x = 320
        mmi.ptMinTrackSize.y = 460
        return 0
    if msg == WM_LBUTTONDOWN:
        if c:
            user32.SetFocus(hwnd)
            user32.SetCapture(hwnd)
            c.on_down(lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
        return 0
    if msg == WM_MOUSEMOVE:
        if c:
            c.on_move(lparam & 0xFFFF, (lparam >> 16) & 0xFFFF, wparam & 0x0001)
        return 0
    if msg == WM_LBUTTONUP:
        if c:
            c.on_up(lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
            user32.ReleaseCapture()
        return 0
    if msg == WM_CHAR:
        if c:
            c.key(chr(wparam))
            c.invalidate()
        return 0
    if msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_REF = WNDPROC(_wndproc)


def main():
    global _CALC

    # 与工作台一致地按物理像素工作，否则嵌入后坐标会被系统再缩放一次
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.style = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = _WNDPROC_REF
    wc.hInstance = hinst
    wc.hIcon = None
    wc.hCursor = user32.LoadCursorW(None, 32512)   # IDC_ARROW
    wc.hbrBackground = None
    wc.lpszClassName = CLASS_NAME
    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err not in (0, 1410):  # 1410 = 类已注册
            raise ctypes.WinError(err)

    hwnd = user32.CreateWindowExW(
        0, CLASS_NAME, TITLE, WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 380, 540,
        None, None, hinst, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    _CALC = Calculator(hwnd)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
