"""
网页抓取工具，简单GET请求

【2026-11-09 迁移】browser_fetch 已搬进 plugins/installed/net_plugin/module.py，
与 web_search 合并成「🌐 联网」插件（工具注册 + 一页面板）。
本文件的函数体保留作回滚参照，main.py 不再从这里 import。
"""
# 迁移说明见上；函数体未改。
import requests
from core.logger import root_logger

def browser_fetch(url: str):
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.text[:4000]
    except Exception as e:
        root_logger.error(f"网页抓取失败 {e}")
        return f"fetch fail: {e}"

plugin_export = {"browser_fetch": {"desc": "网页文本抓取", "func": browser_fetch}}
