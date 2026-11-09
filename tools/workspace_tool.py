"""
视觉工作台工具：让模型「只在一个窗口内」看见并操作桌面软件

与 services/vision_workspace.py 里的原生工作台通过本地 HTTP 通信
（工作台是独立进程，键鼠动作都在它那边执行，坐标基准是舞台而不是整屏）。

用法主线：
    workspace_open("画图")          启动并自动嵌入
    workspace_look("描述工具栏")     截舞台图 + VL 读图
    workspace_click(120, 300)       在舞台内点（0~1000 相对坐标）
    workspace_drag(100,500,900,500) 画一条线
"""
import json
import os
import subprocess
import sys
import time

import requests

from core.logger import root_logger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT_FILE = os.path.join(ROOT, "logs", "vision_workspace.port")
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
TIMEOUT = 3.0

DEFAULT_LOOK_PROMPT = (
    "这是被限制在单个窗口内的视觉工作台画面。请描述：窗口标题是什么；有哪些工具栏/按钮/菜单；"
    "当前画面里最显眼的内容是什么；如果后续要点击某个按钮，它在 0~1000 相对坐标里的位置大约是多少。"
    "看不清就说看不清，不要编造。"
)


def _port():
    try:
        with open(PORT_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _alive(port) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/state", timeout=1.5)
        return r.ok and r.json().get("ok")
    except Exception:
        return False


def _ensure(auto: bool = True):
    """确保工作台在跑。返回 (port, reason)"""
    port = _port()
    if port and _alive(port):
        return port, ""
    if not auto:
        return None, "视觉工作台没在运行（面板第 2 页点「启动视觉工作台」）"
    if not os.path.exists(PYW):
        return None, "找不到 pythonw.exe，无法启动工作台"
    try:
        subprocess.Popen([PYW, "-m", "services.vision_workspace"], cwd=ROOT,
                         creationflags=0x00000008)   # DETACHED_PROCESS
        root_logger.info("[视觉工作台] 已由工具侧拉起")
    except Exception as e:
        return None, f"启动工作台失败：{e}"
    deadline = time.time() + 10
    while time.time() < deadline:
        p = _port()
        if p and _alive(p):
            return p, ""
        time.sleep(0.4)
    return None, "工作台启动超时（窗口应该已经弹出，可稍后再试）"


def _call(method: str, path: str, payload=None):
    port, reason = _ensure(auto=True)
    if not port:
        return {"ok": False, "reason": reason}
    try:
        url = f"http://127.0.0.1:{port}{path}"
        if method == "GET":
            r = requests.get(url, timeout=TIMEOUT)
        else:
            r = requests.post(url, json=payload or {}, timeout=TIMEOUT + 6)
        return r.json()
    except Exception as e:
        return {"ok": False, "reason": f"工作台接口异常：{e}"}


def _fmt(res: dict) -> str:
    return json.dumps(res, ensure_ascii=False)


# ---------------- 工具实现 ----------------

def workspace_status(session_id: str = ""):
    """工作台状态：是否附着、附着什么、舞台矩形"""
    return _fmt(_call("GET", "/state"))


def workspace_list(session_id: str = ""):
    """软件地址管理器里的软件 + 当前可嵌入的桌面窗口"""
    port, reason = _ensure(auto=True)
    if not port:
        return reason
    sw = _call("GET", "/software")
    wins = _call("GET", "/windows")
    names = [i.get("name") for i in (sw.get("items") or [])]
    titles = [f"{w['title']}" for w in (wins.get("windows") or [])][:25]
    return (f"已登记软件：{', '.join(names)}\n"
            f"当前桌面窗口（可嵌入）：{'; '.join(titles) if titles else '无'}")


def workspace_open(name: str = "", session_id: str = ""):
    """启动软件地址管理器里的一个软件，并自动嵌进舞台"""
    if not name:
        return "请给出要打开的软件名，如 workspace_open(\"画图\")"
    try:
        from core.vision_acl_mgr import vision_acl_mgr
        ok, why = vision_acl_mgr.check_workspace_software(name)
        if not ok:
            return f"权限拒绝：{why}"
    except Exception as e:
        root_logger.warning(f"[视觉工作台] 权限校验跳过：{e}")
    res = _call("POST", "/launch", {"name": name})
    if res.get("ok") and res.get("hwnd"):
        return (f"已打开「{name}」并嵌入舞台" if res.get("attached")
                else f"已打开「{name}」，但没能自动嵌入：{res.get('reason', '')}")
    return _fmt(res)


def workspace_attach(title: str = "", session_id: str = ""):
    """把一个已经在桌面上的窗口嵌进舞台（按标题包含匹配）"""
    if not title:
        return "请给出窗口标题的一部分，如 workspace_attach(\"记事本\")"
    return _fmt(_call("POST", "/attach", {"title": title}))


def workspace_detach(session_id: str = ""):
    """把当前软件移出舞台（还原到桌面原位置）"""
    return _fmt(_call("POST", "/detach", {}))


def workspace_look(instruction: str = "", double_sample: bool = False,
                   session_id: str = ""):
    """截舞台图并做 VL 识别——模型「看见」窗口内容的唯一入口"""
    shot = _call("POST", "/shot", {})
    if not shot.get("ok"):
        return f"截图失败：{shot.get('reason', '')}"
    path = shot["path"]
    prompt = instruction.strip() or DEFAULT_LOOK_PROMPT
    try:
        from tools.vl_vision_tool import vl_analyze_image
        return vl_analyze_image(path, session_id or "workspace", prompt,
                                double_sample=bool(double_sample))
    except Exception as e:
        return f"已截图（{path}），但 VL 识别失败：{e}"


def workspace_click(x: float = 500, y: float = 500, button: str = "left",
                    clicks: int = 1, session_id: str = ""):
    """在舞台内点击，坐标 0~1000 相对舞台"""
    res = _call("POST", "/click", {"x": x, "y": y, "button": button, "clicks": clicks})
    return _fmt(res)


def workspace_drag(x1: float = 100, y1: float = 500, x2: float = 900, y2: float = 500,
                   duration: float = 0.4, session_id: str = ""):
    """在舞台内按住拖拽（画图软件画线就用这个），坐标 0~1000"""
    return _fmt(_call("POST", "/drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                        "duration": duration}))


def workspace_type(text: str = "", session_id: str = ""):
    """向舞台里的软件输入文本（中文自动走剪贴板）"""
    return _fmt(_call("POST", "/type", {"text": text}))


def workspace_key(key: str = "enter", session_id: str = ""):
    """在舞台里的软件里按键，如 enter / esc / tab / ctrl+s"""
    return _fmt(_call("POST", "/key", {"key": key}))


def workspace_scroll(delta: float = 2, session_id: str = ""):
    """在舞台中心滚动，正数向下"""
    return _fmt(_call("POST", "/scroll", {"delta": delta}))


plugin_export = {
    "workspace_status": {"desc": "视觉工作台状态：舞台里附着了哪个软件", "func": workspace_status},
    "workspace_list": {"desc": "列出已登记软件与当前可嵌入的桌面窗口", "func": workspace_list},
    "workspace_open": {"desc": "启动已登记的软件（记事本/画图等）并自动嵌入视觉工作台", "func": workspace_open},
    "workspace_attach": {"desc": "按窗口标题把桌面上的软件嵌进视觉工作台舞台", "func": workspace_attach},
    "workspace_detach": {"desc": "把当前软件移出舞台，还原到桌面原位", "func": workspace_detach},
    "workspace_look": {"desc": "截取舞台画面并 VL 识别，是看见该软件内容的唯一入口", "func": workspace_look},
    "workspace_click": {"desc": "在舞台内点击，坐标 0~1000 相对舞台", "func": workspace_click},
    "workspace_drag": {"desc": "在舞台内按住拖拽（在画图里画线），坐标 0~1000", "func": workspace_drag},
    "workspace_type": {"desc": "向舞台里的软件输入文本（中文走剪贴板）", "func": workspace_type},
    "workspace_key": {"desc": "在舞台里的软件里按键，如 enter/esc/ctrl+s", "func": workspace_key},
    "workspace_scroll": {"desc": "在舞台中心滚动，正数向下", "func": workspace_scroll},
}
