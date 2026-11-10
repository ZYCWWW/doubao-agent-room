# -*- coding: utf-8 -*-
"""
看门狗：检查面板 / Ollama / 长期闲聊三个服务，缺哪个拉哪个，跑完就退出。

设计成"一次性检查"而不是常驻循环，由计划任务每隔几分钟调用一次——
这样看门狗自己挂了也不会连累服务，下一次检查自然把它补上来。

用法：
    python tools/watchdog.py            # 检查并修复
    python tools/watchdog.py --dry      # 只检查不启动

日志：logs/watchdog.log
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "watchdog.log")
PANEL_PORT = 8501
OLLAMA_PORT = 11434
OLLAMA_EXE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
PYTHON = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_ollama(dry: bool) -> str:
    if port_open(OLLAMA_PORT):
        return "ollama 在"
    if dry:
        return "ollama 缺（dry）"
    if not os.path.exists(OLLAMA_EXE):
        return "ollama 缺，且找不到 exe"
    subprocess.Popen(
        [OLLAMA_EXE, "serve"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "ollama 已拉起"


def ensure_panel(dry: bool) -> str:
    if port_open(PANEL_PORT):
        return "面板在"
    if dry:
        return "面板缺（dry）"
    if not os.path.exists(PYTHON):
        return "面板缺，且找不到 venv python"
    out = open(os.path.join(ROOT, "logs", "panel_launch.out.log"), "ab")
    err = open(os.path.join(ROOT, "logs", "panel_launch.err.log"), "ab")
    subprocess.Popen(
        [PYTHON, "-m", "streamlit", "run", os.path.join(ROOT, "dashboard.py"),
         "--server.headless", "true", "--server.port", str(PANEL_PORT),
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "面板已拉起"


def ensure_long_chat(dry: bool) -> str:
    try:
        sys.path.insert(0, ROOT)
        os.chdir(ROOT)
        from services import long_chat as lc
        st = lc.status()
        if st.get("running"):
            return f"闲聊在（pid={st.get('pid')}）"
        if dry:
            return "闲聊缺（dry）"
        r = lc.long_chat_start()
        return f"闲聊已拉起 {r}"
    except Exception as e:
        return f"闲聊检查失败: {str(e)[:120]}"


def main() -> None:
    dry = "--dry" in sys.argv
    log(f"--- 检查开始{dry and '（dry）' or ''} ---")
    for step in (ensure_ollama, ensure_panel, ensure_long_chat):
        try:
            log(step(dry))
        except Exception as e:
            log(f"{step.__name__} 异常: {str(e)[:120]}")
    log("--- 检查结束 ---")


if __name__ == "__main__":
    main()
