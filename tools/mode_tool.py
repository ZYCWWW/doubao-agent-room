# tools/mode_tool.py
"""双模式工具：mode_status / mode_set（用户手动指定模式，强标签）

已迁移：2026-11-09 起不再内置注册，改由功能插件
`plugins/installed/mode_plugin/module.py` 导出（两个函数体逐字照搬），
面板页 11 的「运行模式」tab 也已搬成该插件的自带页。
本文件保留为回滚参考，main.py 与 dashboard.py 不再 import 它。
业务本体 `core/mode/strategy.py` 未改动。
"""
from core.mode import mode_strategy, MODES


def _mode_status(session_id: str = ""):
    st = mode_strategy.status()
    return (f"当前模式：{st['predict']} | 手动指定：{st['manual_mode'] or '无'}\n"
            f"样本数：{st['samples']}（steady {st['steady']} / inquiry {st['inquiry']}）\n"
            f"可用模式：{'/'.join(MODES)}（mode_set 可手动指定）")


def _mode_set(mode: str, task: str = "", session_id: str = ""):
    if mode not in MODES:
        return f"非法模式：{mode}（可选：{'/'.join(MODES)}）"
    mode_strategy.set_manual_mode(mode)
    if task:
        mode_strategy.record_choice(task, mode, score=1.0)
    return f"已切换为 {mode} 模式" + ("，并记录为强标签" if task else "")


plugin_export = {
    "mode_status": {"desc": "查看当前运行模式（steady/inquiry）与策略学习状态", "func": _mode_status},
    "mode_set": {"desc": "手动指定运行模式（steady/inquiry）；传 task 时记录为强标签", "func": _mode_set},
}
