# core/plugin/base.py
"""
PluginBase 规范：
- ToolSpec：name/desc/params(JSON Schema 简化 dict)/func/dangerous
- PluginBase：插件生命周期（load/unload/on_enable/on_disable）
- 现有 tools/*.py 的 plugin_export 可包装为内置插件（工具名不变，兼容层双轨）
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from abc import ABC, abstractmethod


@dataclass
class ToolSpec:
    name: str
    desc: str
    func: Callable
    params: dict = field(default_factory=dict)   # 简化 JSON Schema：{"field": {"type": "str", "required": false}}
    dangerous: bool = False                       # 高危标记：触发审批/风险检查


class PluginBase(ABC):
    name: str = ""
    version: str = "0.1"
    desc: str = ""
    source: str = "builtin"                       # builtin / local / market
    tools: dict = {}                              # name -> ToolSpec
    config: dict = {}

    @abstractmethod
    def load(self) -> bool:
        """校验 manifest、依赖、导入；失败返回 False（由 registry 标记 disabled）"""

    def unload(self) -> None:
        pass

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass
