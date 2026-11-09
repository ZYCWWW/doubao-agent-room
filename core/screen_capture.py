"""
屏幕截图 + DPI坐标换算
解决Windows高DPI缩放：截图像素坐标 ≠ 系统鼠标坐标
"""
try:
    import pyautogui
except Exception:
    # Linux 无图形环境（没有 DISPLAY）时 pyautogui 导入即失败。
    # 这里降级为 None，让纯文本功能照常可用，只有真正要截屏/控鼠标时才报错。
    pyautogui = None
import os
import uuid
from core.config_loader import config_loader
from core.logger import root_logger
import ctypes

DPI_FIX = config_loader["dpi"]["enable_dpi_fix"]

def get_dpi_scale():
    """获取系统DPI缩放比例"""
    if not DPI_FIX or pyautogui is None:
        return 1.0
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        w, h = pyautogui.size()
        real_w = ctypes.windll.user32.GetSystemMetrics(0)
        return real_w / w
    except Exception:
        root_logger.warning("DPI缩放获取失败，使用1.0比例")
        return 1.0

DPI_SCALE = get_dpi_scale()

def capture_screen(session_id: str) -> str:
    """
    截图，保存到当前会话沙盘目录
    :return: 截图绝对路径
    """
    if pyautogui is None:
        raise RuntimeError("当前环境没有可用的图形界面，无法截图（需要 Windows 桌面或带显示器的 Linux）")
    sandbox_root = config_loader["sandbox"]["root_path"]
    out_dir = os.path.join(sandbox_root, session_id)
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.png"
    path = os.path.join(out_dir, fname)
    im = pyautogui.screenshot()
    im.save(path)
    return path

def scale_pos(x, y):
    """把VL识别到的截图坐标换算成真实系统鼠标坐标"""
    return int(x / DPI_SCALE), int(y / DPI_SCALE)
