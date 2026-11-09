"""
桌面键鼠、文件打开、窗口管理工具
安全机制：**不自动执行**，全部进入会话隔离审批队列（sqlite 持久化），人工在dashboard确认后才执行
⚠️ 旧版队列在内存（重启清空），现改为 sqlite 持久队列（重启不丢、超时自动拒绝）
兼容：GLOBAL_QUEUE 保留为内存视图，供 dashboard 读取
"""
try:
    import pyautogui
except Exception:
    # Linux 无图形环境（没有 DISPLAY）时导入即失败。降级为 None，
    # 保证 main / dashboard 能正常启动，只有真要点鼠标时才报错。
    pyautogui = None
import uuid
from core.vision_acl_mgr import vision_acl_mgr
from core.screen_capture import scale_pos
from core.logger import root_logger, audit
from core.config_loader import config_loader
from core.desktop.queue import desktop_queue

# 全局审批队列视图（key=session_id），从 sqlite 同步
GLOBAL_QUEUE = {}
MAX_CONTINUOUS = 10


def _sync_global():
    """把 sqlite 持久队列同步到内存视图（dashboard 读 GLOBAL_QUEUE 用）"""
    global GLOBAL_QUEUE
    GLOBAL_QUEUE = desktop_queue.to_global_view()


def _check_acl(action_type: str, payload: dict, session_id: str) -> tuple:
    """ACL 校验，返回 (permit, reason)；open_file/launch 类检查路径"""
    if action_type in ("open_file", "launch_app"):
        target = payload.get("file_path") or payload.get("app_path")
        if target:
            permit, reason = vision_acl_mgr.check_action_permit(target, "launch_app")
            return permit, reason
    if action_type in ("write_file", "save_file"):
        target = payload.get("file_path")
        if target:
            permit, reason = vision_acl_mgr.check_action_permit(target, "write_file")
            return permit, reason
    if action_type in ("click", "type_text", "scroll", "drag", "hotkey"):
        # 键鼠操作：若指定了 ACL 作用域路径（地址表区域）则校验动作级权限；
        # 未指定时由审批队列兜底（与旧行为一致）
        scope = payload.get("scope_path")
        if scope:
            permit, reason = vision_acl_mgr.check_action_permit(scope, "mouse_keyboard")
            return permit, reason
        return True, ""
    return True, ""


def desktop_action(action_type: str, payload: dict, session_id: str):
    """
    申请桌面操作，加入审批队列（sqlite 持久）
    action_type: open_file / type_text / click / scroll / drag / hotkey / window_list / window_activate / window_close
    """
    payload = payload or {}
    permit, reason = _check_acl(action_type, payload, session_id)
    if not permit:
        audit(f"DESKTOP_DENY | sid={session_id} | action={action_type} | {reason}")
        return f"❌ ACL拦截：{reason}"

    aid = desktop_queue.enqueue(session_id, action_type, payload)
    _sync_global()
    audit(f"DESKTOP_QUEUE | sid={session_id} | aid={aid} | action={action_type}")
    return f"✅ 操作已入审批队列（持久），aid={aid}，等待用户确认"


def execute_approved(aid: str, sid: str):
    """dashboard面板批准后，调用这个函数真正执行操作"""
    item = desktop_queue.get(aid)
    if not item:
        return "找不到该审批项"
    if item["sid"] != sid:
        return "❌ 会话不匹配，禁止跨会话执行"
    if item["status"] != "pending":
        return f"该项状态为 {item['status']}，不可执行"
    t = item["action_type"]
    p = item["payload"] or {}
    if pyautogui is None and t in ("type_text", "click", "scroll", "hotkey"):
        return "❌ 当前环境没有可用的图形界面，键鼠类操作无法执行（需要 Windows 桌面或带显示器的 Linux）"
    try:
        if t == "open_file":
            import os
            os.startfile(p["file_path"])
        elif t == "type_text":
            import re as _re
            text = p["text"]
            parts = _re.split(r"(\{[A-Za-z0-9]+\})", text)
            for part in parts:
                if _re.fullmatch(r"\{[A-Za-z0-9]+\}", part):
                    pyautogui.press(part[1:-1].lower())
                elif part:
                    pyautogui.typewrite(part)
        elif t == "click":
            x, y = scale_pos(p["x"], p["y"])
            pyautogui.click(x, y)
        elif t == "scroll":
            pyautogui.scroll(int(p.get("clicks", -3)), x=p.get("x"), y=p.get("y"))
        elif t == "hotkey":
            pyautogui.hotkey(*[k.strip() for k in (p.get("keys") or "ctrl,s").split(",")])
        elif t == "window_activate":
            from core.desktop.window import activate_window
            if not activate_window(p.get("title", "")):
                return "窗口未找到或激活失败"
        elif t == "window_close":
            from core.desktop.window import close_window
            if not close_window(p.get("title", "")):
                return "窗口未找到或关闭失败"
        elif t == "window_list":
            from core.desktop.window import list_windows
            wins = list_windows(p.get("filter"))
            desktop_queue.update_status(aid, "done")
            _sync_global()
            return "窗口列表：\n" + "\n".join(
                f"- {w['title']} ({w['width']}x{w['height']})" for w in wins)
        else:
            return f"未知动作类型：{t}"
        desktop_queue.update_status(aid, "done")
        _sync_global()
        audit(f"DESKTOP_EXEC | sid={sid} | aid={aid} | action={t}")
        return "执行成功"
    except Exception as e:
        desktop_queue.update_status(aid, "failed")
        _sync_global()
        audit(f"DESKTOP_FAIL | sid={sid} | aid={aid} | err={e}")
        return f"执行失败：{e}"


def desktop_queue_status(session_id: str = None, **kwargs):
    """查看审批队列（含超时自动拒绝后状态）"""
    desktop_queue.auto_reject_timeout(session_id)
    items = desktop_queue.list_queue(sid=session_id)
    if not items:
        return "审批队列为空"
    lines = [f"- [{it['status']}] {it['action_type']} aid={it['aid']} sid={it['sid']} payload={it['payload']}"
             for it in items]
    return "审批队列：\n" + "\n".join(lines)


# 启动时同步一次持久队列到内存视图
_sync_global()

plugin_export = {
    "desktop_action": {"desc": "键鼠/文件/窗口操作，进入持久审批队列，等待人工确认。"
                               "action_type: open_file/type_text/click/scroll/hotkey/window_list/window_activate/window_close",
                        "func": desktop_action},
    "desktop_queue_status": {"desc": "查看桌面审批队列状态（含超时自动拒绝结果）", "func": desktop_queue_status},
}
