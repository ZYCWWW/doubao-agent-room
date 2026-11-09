# sample_market_plugin/module.py
"""市场插件示例：倒计时（演示 market 来源插件的加载）"""
import time


def countdown(seconds: int = 5, session_id: str = ""):
    seconds = max(1, min(60, int(seconds)))
    for i in range(seconds, 0, -1):
        time.sleep(1)
        print(f"倒计时 {i}s")
    return f"倒计时结束（{seconds}s）"


plugin_export = {
    "countdown": {"desc": "阻塞倒计时 seconds 秒（示例市场插件）", "func": countdown},
}
