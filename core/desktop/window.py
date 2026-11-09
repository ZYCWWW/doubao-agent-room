"""
窗口管理原语（基于 pyautogui 的窗口枚举/激活/关闭）
- list_windows / activate_window / close_window / list_app_windows
"""
import os
from core.logger import root_logger

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:
    _HAS_PYAUTOGUI = False


def list_windows(title_filter: str = None) -> list:
    """枚举窗口：{title, left, top, width, height, active}"""
    if not _HAS_PYAUTOGUI:
        return []
    try:
        wins = pyautogui.getAllWindows()
    except Exception as e:
        root_logger.warning(f"窗口枚举失败: {e}")
        return []
    out = []
    for w in wins:
        title = (w.title or "").strip()
        if not title:
            continue
        if title_filter and title_filter.lower() not in title.lower():
            continue
        try:
            out.append({
                "title": title,
                "left": w.left, "top": w.top,
                "width": w.width, "height": w.height,
            })
        except Exception:
            continue
    return out


def activate_window(title: str) -> bool:
    """激活标题包含指定文本的窗口"""
    if not _HAS_PYAUTOGUI:
        return False
    try:
        wins = pyautogui.getWindowsWithTitle(title)
        if not wins:
            root_logger.warning(f"窗口未找到: {title}")
            return False
        wins[0].activate()
        return True
    except Exception as e:
        root_logger.warning(f"窗口激活失败 {title}: {e}")
        return False


def close_window(title: str) -> bool:
    """关闭标题包含指定文本的窗口"""
    if not _HAS_PYAUTOGUI:
        return False
    try:
        wins = pyautogui.getWindowsWithTitle(title)
        if not wins:
            root_logger.warning(f"窗口未找到: {title}")
            return False
        wins[0].close()
        return True
    except Exception as e:
        root_logger.warning(f"窗口关闭失败 {title}: {e}")
        return False
