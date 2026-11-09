"""
想象沙盘SQLite索引
沙盘原始文件是MD/图片，sqlite只存摘要、标签、关联记忆/技能id，加速检索
不会把全部文件灌入向量库，节省资源
"""
import sqlite3
import os
from core.config_loader import config_loader
from core.logger import root_logger

DB_ROOT = config_loader["sandbox"]["root_path"]

def get_sandbox_index_db(session_id: str):
    """获取当前会话的沙盘索引数据库连接，自动建表"""
    db_path = os.path.join(DB_ROOT, session_id, "sandbox_index.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sandbox_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT,
        file_path TEXT,
        file_type TEXT,
        summary TEXT,
        tag TEXT,
        related_skill_id TEXT,
        related_memory_id TEXT,
        create_ts REAL
    )
    """)
    conn.commit()
    return conn

def index_add_file(session_id: str, file_path: str, ftype: str, summary: str, tag="", skill_id=None, mem_id=None):
    """新增一条沙盘文件索引记录"""
    conn = get_sandbox_index_db(session_id)
    cur = conn.cursor()
    import time
    cur.execute("""
    INSERT INTO sandbox_index (file_name, file_path, file_type, summary, tag, related_skill_id, related_memory_id, create_ts)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (os.path.basename(file_path), file_path, ftype, summary, tag, skill_id, mem_id, time.time()))
    conn.commit()
    conn.close()

def index_search(session_id: str, keyword: str):
    """在当前会话沙盘索引内按关键词检索摘要和标签"""
    conn = get_sandbox_index_db(session_id)
    cur = conn.cursor()
    res = cur.execute("SELECT * FROM sandbox_index WHERE summary LIKE ? OR tag LIKE ?", (f"%{keyword}%", f"%{keyword}%"))
    rows = res.fetchall()
    conn.close()
    return rows
