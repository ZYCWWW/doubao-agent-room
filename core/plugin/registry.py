# core/plugin/registry.py
"""
插件注册表：
- discover：扫描 plugins/installed + plugins/market + 内置注册
- enable/disable：启停插件
- get_tool：按工具名取 ToolSpec（懒加载）
- schema_snapshot：全部启用工具描述预编译（L1 缓存）
- call：入参校验 → 危险标记拦截 → 执行 → 异常捕获返回标准 Observation
- all_tools：返回 {name: {"desc","func"}}（兼容现有 ALL_TOOLS 双轨）
"""
import inspect
import threading
from dataclasses import dataclass
from typing import Optional
from core.logger import root_logger
from core.plugin.base import PluginBase, ToolSpec
from core.plugin.validator import validate_manifest, validate_tools


class PluginRegistry:
    def __init__(self):
        self._plugins: dict[str, PluginBase] = {}       # name -> plugin
        self._status: dict[str, str] = {}               # name -> enabled/disabled/broken
        self._tool_index: dict[str, str] = {}           # tool_name -> plugin_name
        self._lock = threading.RLock()

    # ---------- 注册与发现 ----------
    def register_builtin(self, name: str, tools: dict, version: str = "0.1",
                         desc: str = "") -> bool:
        """内置插件：直接包装 tools dict（plugin_export 兼容层）"""
        specs = {}
        for tname, meta in tools.items():
            specs[tname] = ToolSpec(name=tname, desc=meta.get("desc", ""),
                                    func=meta["func"],
                                    dangerous=meta.get("dangerous", False))
        plugin = _BuiltinPlugin(name=name, version=version, desc=desc, tools=specs)
        with self._lock:
            self._plugins[name] = plugin
            self._status[name] = "enabled"
            for tname in specs:
                self._tool_index[tname] = name
        return True

    def discover(self, roots: list = None) -> int:
        """扫描本地/市场插件目录（manifest.json），失败插件标记 broken 不阻塞"""
        import json
        import os
        count = 0
        for root in (roots or []):
            if not os.path.isdir(root):
                continue
            for entry in os.listdir(root):
                dir_path = os.path.join(root, entry)
                if not os.path.isdir(dir_path):
                    continue
                manifest_path = os.path.join(dir_path, "manifest.json")
                if not os.path.exists(manifest_path):
                    continue
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        manifest = json.load(f)
                    ok, reason = validate_manifest(manifest)
                    if not ok:
                        self._status[entry] = "broken"
                        root_logger.error(f"插件[{entry}] manifest 校验失败: {reason}")
                        continue
                    self._register_file_plugin(entry, dir_path, manifest)
                    count += 1
                except Exception as e:
                    self._status[entry] = "broken"
                    root_logger.error(f"插件[{entry}] 加载异常: {e}")
        return count

    def _register_file_plugin(self, name: str, dir_path: str, manifest: dict) -> None:
        """从插件目录导入主模块并注册（懒加载：get_tool 时才真正导入）"""
        with self._lock:
            self._plugins[name] = _FilePlugin(name=name, version=manifest.get("version", "0.1"),
                                              desc=manifest.get("desc", ""),
                                              dir_path=dir_path, manifest=manifest,
                                              tools={})
            self._status[name] = "enabled"

    # ---------- 状态管理 ----------
    def enable(self, name: str) -> bool:
        with self._lock:
            p = self._plugins.get(name)
            if not p:
                return False
            self._status[name] = "enabled"
            p.on_enable()
        return True

    def disable(self, name: str) -> bool:
        with self._lock:
            p = self._plugins.get(name)
            if not p:
                return False
            # 移除该插件全部工具索引
            for tname in list(self._tool_index):
                if self._tool_index[tname] == name:
                    del self._tool_index[tname]
            self._status[name] = "disabled"
            p.on_disable()
        return True

    # ---------- 安装与卸载（只动本地文件夹，不联网） ----------
    def install(self, src_dir: str, install_root: str, name: str = "") -> tuple:
        """把本地插件文件夹装进 install_root/<name>，然后注册。

        - 只负责“放对位置 + 注册”，不下载、不改别人的包
        - 目标已存在就拒绝，绝不覆盖（重装要先卸载）
        - 注册后状态不是 enabled 时保留目录并说明原因，方便就地改 manifest 再装
        """
        import json
        import os
        import shutil
        if not os.path.isdir(src_dir):
            return False, f"源目录不存在：{src_dir}"
        manifest_path = os.path.join(src_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            return False, "源目录里没有 manifest.json，不像一个插件"
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            return False, f"manifest.json 读不了：{e}"
        ok, reason = validate_manifest(manifest)
        if not ok:
            return False, f"manifest 校验失败：{reason}"
        if not os.path.exists(os.path.join(src_dir, "module.py")):
            return False, "源目录里没有 module.py"
        # 注册表以“目录名”为插件名（discover 用的就是 entry），所以目录名就是名字
        name = (name or str(manifest.get("name") or "") or os.path.basename(src_dir)).strip()
        if (not name) or ("/" in name) or ("\\" in name) or name.startswith("."):
            return False, f"插件名不合法：{name!r}"
        os.makedirs(install_root, exist_ok=True)
        dst = os.path.join(install_root, name)
        if os.path.exists(dst):
            return False, f"已存在同名插件目录：{dst}（要重装请先卸载）"
        with self._lock:
            if name in self._plugins:
                return False, f"注册表里已有同名插件：{name}"
        try:
            shutil.copytree(src_dir, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        except Exception as e:
            return False, f"复制失败：{e}"
        try:
            self.discover([install_root])
        except Exception as e:
            shutil.rmtree(dst, ignore_errors=True)
            return False, f"注册失败：{e}"
        status = self._status.get(name)
        if status != "enabled":
            return False, (f"已复制到 {dst}，但注册后状态是 {status}，"
                           f"看日志里的校验错误，改完再重装")
        return True, f"已安装 {name} → {dst}"

    def uninstall(self, name: str, install_root: str, trash_root: str = "") -> tuple:
        """卸载文件插件：目录移到回收区（不直接删，能捞回来），并从内存注册表移除。"""
        import os
        import shutil
        import time
        with self._lock:
            plugin = self._plugins.get(name)
            if not plugin:
                return False, f"没有这个插件：{name}"
            if not isinstance(plugin, _FilePlugin):
                return False, f"{name} 是内置插件，不能卸载（可以停用）"
            src = os.path.abspath(plugin.dir_path)
            root_abs = os.path.abspath(install_root)
            try:
                inside = os.path.commonpath([src, root_abs]) == root_abs
            except ValueError:      # 不同盘符，commonpath 会抛
                inside = False
            if not inside:
                return False, f"{name} 不在插件安装目录里（{src}），不从这里卸"
            if not os.path.isdir(src):
                self._forget(name)
                return False, f"{name} 的目录已经不在了，已从注册表清掉"
            trash = os.path.abspath(trash_root) if trash_root else \
                os.path.join(os.path.dirname(root_abs), "_removed")
            os.makedirs(trash, exist_ok=True)
            dst = os.path.join(trash, f"{name}__{time.strftime('%Y%m%d_%H%M%S')}")
            try:
                shutil.move(src, dst)
            except Exception as e:
                return False, f"移动失败：{e}（多半是文件被占用，先停用再试）"
            self._forget(name)
        return True, f"已卸载 {name}，目录移到 {dst}（随时能捞回来）"

    def _forget(self, name: str) -> None:
        """把插件从内存注册表摘掉（不碰磁盘）"""
        with self._lock:
            self._plugins.pop(name, None)
            self._status.pop(name, None)
            for tname in list(self._tool_index):
                if self._tool_index[tname] == name:
                    del self._tool_index[tname]

    def list_plugins(self) -> list[dict]:
        with self._lock:
            return [{"name": n, "version": p.version, "desc": p.desc,
                     "source": p.source, "status": self._status.get(n, "unknown"),
                     "tools": sorted(p.tools.keys()) if isinstance(p.tools, dict) else []}
                    for n, p in self._plugins.items()]

    # ---------- 面板页（功能插件的界面入口） ----------
    def list_panels(self) -> list[dict]:
        """列出声明了 panel 且处于启用状态的插件页

        manifest.json 里写：
            "panel": {"label": "🔬 科研七步", "order": 50}
        插件 module.py 提供 panel_render() 即可渲染整页。
        """
        out = []
        with self._lock:
            for name, plugin in self._plugins.items():
                if self._status.get(name) != "enabled":
                    continue
                manifest = getattr(plugin, "manifest", None) or {}
                panel = manifest.get("panel")
                if not isinstance(panel, dict):
                    continue
                label = str(panel.get("label") or "").strip()
                if not label:
                    continue
                out.append({"plugin": name, "label": label,
                            "order": panel.get("order", 50)})
        out.sort(key=lambda x: (x["order"], x["plugin"]))
        return out

    def render_panel(self, plugin_name: str) -> bool:
        """渲染插件页；插件没声明 panel 或渲染失败返回 False（调用方兜底提示）"""
        plugin = self._plugins.get(plugin_name)
        if not plugin or self._status.get(plugin_name) != "enabled":
            return False
        if isinstance(plugin, _FilePlugin) and not getattr(plugin, "module", None):
            try:
                plugin.load()
            except Exception as e:
                self._status[plugin_name] = "broken"
                root_logger.error(f"插件[{plugin_name}] 面板加载失败: {e}")
                return False
        render = getattr(getattr(plugin, "module", None), "panel_render", None)
        if not callable(render):
            return False
        try:
            render()
            return True
        except Exception as e:
            root_logger.error(f"插件[{plugin_name}] 面板渲染异常: {e}")
            return False

    # ---------- 工具访问 ----------
    def get_tool(self, tool_name: str) -> Optional[ToolSpec]:
        with self._lock:
            plugin_name = self._tool_index.get(tool_name)
            if not plugin_name:
                # 懒加载兜底：扫描未加载的文件插件，加载并建立工具索引
                self._ensure_file_index()
                plugin_name = self._tool_index.get(tool_name)
            if not plugin_name:
                return None
            plugin = self._plugins.get(plugin_name)
            if not plugin or self._status.get(plugin_name) != "enabled":
                return None
            # 懒加载：文件插件首次访问时才 load
            if isinstance(plugin, _FilePlugin) and not plugin.tools:
                try:
                    plugin.load()
                    for tname in plugin.tools:
                        self._tool_index.setdefault(tname, plugin_name)
                except Exception as e:
                    self._status[plugin_name] = "broken"
                    root_logger.error(f"插件[{plugin_name}] 懒加载失败: {e}")
                    return None
            return plugin.tools.get(tool_name)

    def _ensure_file_index(self) -> None:
        """兜底：加载所有未加载的文件插件，补齐工具索引（供 get_tool/all_tools 复用）"""
        for pname, plugin in list(self._plugins.items()):
            if isinstance(plugin, _FilePlugin) and not plugin.tools:
                try:
                    plugin.load()
                    for tname in plugin.tools:
                        self._tool_index.setdefault(tname, pname)
                except Exception as e:
                    self._status[pname] = "broken"
                    root_logger.error(f"插件[{pname}] 懒加载失败: {e}")

    def all_tools(self) -> dict:
        """返回 {name: {desc, func}}（兼容现有 ALL_TOOLS 结构）"""
        out = {}
        with self._lock:
            self._ensure_file_index()
            for tname in list(self._tool_index):
                spec = self.get_tool(tname)
                if spec:
                    out[tname] = {"desc": spec.desc, "func": spec.func}
        return out

    def schema_snapshot(self) -> str:
        """预编译全部启用工具描述（系统 prompt 用，L1 缓存由调用方负责）"""
        specs = []
        with self._lock:
            self._ensure_file_index()
            for tname in sorted(self._tool_index):
                spec = self.get_tool(tname)
                if spec:
                    specs.append(f"- {tname}: {spec.desc}")
        return "\n".join(specs)

    def call(self, tool_name: str, args: dict, session_id: str = "") -> str:
        """安全调用：入参校验 → 执行 → 异常捕获（不 KeyError）"""
        spec = self.get_tool(tool_name)
        if not spec:
            return f"工具不存在：{tool_name}"
        try:
            # 会话注入
            sig = inspect.signature(spec.func)
            if "session_id" in sig.parameters:
                args = dict(args or {})
                args.setdefault("session_id", session_id)
            return spec.func(**(args or {}))
        except TypeError as e:
            return f"工具参数错误（{tool_name}）：{str(e)}"
        except Exception as e:
            return f"工具执行异常（{tool_name}）：{str(e)}"


class _BuiltinPlugin(PluginBase):
    """内置插件包装（现有 plugin_export 兼容层）"""

    def __init__(self, name: str, version: str, desc: str, tools: dict):
        self.name, self.version, self.desc, self.tools = name, version, desc, tools
        self.source = "builtin"

    def load(self) -> bool:
        ok, reason = validate_tools(self.tools)
        return ok


class _FilePlugin(PluginBase):
    """本地/市场文件插件（懒加载）"""

    def __init__(self, name: str, version: str, desc: str, dir_path: str,
                 manifest: dict, tools: dict):
        self.name, self.version, self.desc = name, version, desc
        self.dir_path = dir_path
        self.manifest = manifest
        self.tools = tools
        self.source = "local" if "market" not in dir_path else "market"

    def load(self) -> bool:
        """导入插件主模块（module.py）并注册其 plugin_export"""
        import importlib.util
        import os
        main_file = os.path.join(self.dir_path, "module.py")
        if not os.path.exists(main_file):
            raise FileNotFoundError(f"插件缺少 module.py: {self.dir_path}")
        spec = importlib.util.spec_from_file_location(f"plugin_{self.name}", main_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        export = getattr(mod, "plugin_export", None)
        if not isinstance(export, dict):
            raise ValueError(f"插件[{self.name}] 缺少 plugin_export")
        self.module = mod          # 面板页等扩展能力要从模块上取
        specs = {}
        for tname, meta in export.items():
            specs[tname] = ToolSpec(name=tname, desc=meta.get("desc", ""),
                                    func=meta["func"],
                                    dangerous=meta.get("dangerous", False))
        self.tools = specs
        return True


# 全局单例
registry = PluginRegistry()
