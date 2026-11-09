# core/plugin/loader.py
"""
插件热加载：轮询监听 plugins/installed 与 plugins/market 目录变更
- 目录 mtime 快照变化 → registry.discover() 增量注册
- 失败插件标记 broken，不阻塞主循环
- 原子性：注册表内部持锁，不影响进行中的 ReAct 调用
"""
import os
import threading
import time
from core.async_pool import async_pool
from core.logger import root_logger
from core.plugin.registry import registry

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROOTS = [
    os.path.join(PROJECT_ROOT, "plugins", "installed"),
    os.path.join(PROJECT_ROOT, "plugins", "market"),
]


class PluginHotLoader:
    def __init__(self, roots: list = None, interval: float = 5.0):
        self.roots = [r for r in (roots or DEFAULT_ROOTS) if os.path.isdir(r)]
        self.interval = max(2.0, interval)
        self._snapshot: dict[str, tuple[float, int]] = {}
        self._started = False

    def _dir_snapshot(self) -> dict:
        snap = {}
        for root in self.roots:
            for entry in os.listdir(root):
                p = os.path.join(root, entry)
                if os.path.isdir(p):
                    manifest = os.path.join(p, "manifest.json")
                    if os.path.exists(manifest):
                        try:
                            mtime = os.path.getmtime(manifest)
                            count = len([f for f in os.listdir(p) if f.endswith(".py")])
                            snap[entry] = (mtime, count)
                        except OSError:
                            pass
        return snap

    def scan_once(self) -> int:
        """扫描一次：发现新增/变更插件，返回新注册数"""
        snap = self._dir_snapshot()
        changed = [name for name in snap
                   if name not in self._snapshot or snap[name] != self._snapshot.get(name)]
        self._snapshot = snap
        if not changed:
            return 0
        n = registry.discover(self.roots)
        # 插件变更后失效工具描述缓存，保证下次会话的 system prompt 含最新工具列表
        try:
            from core.cache.schema_cache import invalidate_schema
            invalidate_schema("tool_desc_v2")
        except Exception:
            pass
        root_logger.info(f"插件热加载扫描：{len(changed)} 个变更，注册 {n} 个")
        return n

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._snapshot = self._dir_snapshot()
        async_pool.schedule_periodic(self.scan_once, self.interval, "plugin_hotloader",
                                     run_immediately=False)
        root_logger.info(f"插件热加载已启动（interval={self.interval}s，roots={len(self.roots)}）")

    def stop(self) -> None:
        async_pool.stop_periodic("plugin_hotloader")
        self._started = False


# 全局单例
hot_loader = PluginHotLoader()
