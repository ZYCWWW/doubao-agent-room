# core/logger.py
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 项目根目录：本文件位于 <项目根>/core/logger.py，日志固定落在项目根/logs，与启动目录无关
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 根日志实例
root_logger = logging.getLogger("agent")
root_logger.setLevel(logging.INFO)

# 防止重复添加handler
if not root_logger.handlers:
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # 控制台输出
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # 文件输出
    # 日志轮转：单文件 2MB，保留 3 个历史文件（数据治理）
    file_handler = RotatingFileHandler(LOG_DIR / "agent.log", maxBytes=2 * 1024 * 1024,
                                       backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

# 审计日志快捷函数（你main里import了audit，保留）
def audit(msg: str):
    root_logger.info(f"[AUDIT] {msg}")
