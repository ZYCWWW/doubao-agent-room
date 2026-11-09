# core/model_router.py
"""
多模型路由：任务类型 → 模型名
- 配置段 [models] + [models.routing]（config.toml，面板可改）
- 未配置回落：main→ollama.chat_model；vision→ollama.vision_model；light→qwen2.5:3b
"""
from typing import Optional
from core.config_loader import config_loader

# 默认路由（轻任务用轻模型）
DEFAULT_ROUTING = {
    "main": None,              # 回落 chat_model
    "vision": None,            # 回落 vision_model
    "light": "qwen2.5:3b",
    "score": "light",
    "summary": "light",
    "prompt_optimize": "light",
    "debate": "main",
    "reflection": "light",
    "conflict_check": "light",
    "causal_check": "main",
    "dimension_derive": "main",
}


class ModelRouter:
    def __init__(self):
        self._models: dict[str, str] = {}
        self._routing: dict[str, str] = {}
        self._reload()

    def _reload(self) -> None:
        models_cfg = config_loader.get("models")
        if isinstance(models_cfg, dict):
            self._models = {
                "main": str(models_cfg.get("main") or models_cfg.get("main_model") or ""),
                "light": str(models_cfg.get("light") or models_cfg.get("light_model") or ""),
                "vision": str(models_cfg.get("vision") or models_cfg.get("vision_model") or ""),
            }
            routing = models_cfg.get("routing")
            if isinstance(routing, dict):
                self._routing = {str(k): str(v) for k, v in routing.items()}
        # 从 ollama 段回落
        ollama = config_loader.get("ollama", {})
        if not self._models.get("main"):
            self._models["main"] = ollama.get("chat_model", "deepseekR1Q7B:latest")
        if not self._models.get("vision"):
            self._models["vision"] = ollama.get("vision_model", "qwen3.5:4b")
        # light 模型内置默认（config 未配置时用轻量模型跑轻任务）
        if not self._models.get("light"):
            self._models["light"] = DEFAULT_ROUTING.get("light", "qwen2.5:3b")

    # ---- 查询 ----
    def route(self, task_type: str) -> str:
        """任务类型 → 实际模型名"""
        if task_type in self._models and self._models[task_type]:
            return self._models[task_type]
        target = self._routing.get(task_type, DEFAULT_ROUTING.get(task_type, "main"))
        if target in self._models and self._models[target]:
            return self._models[target]
        if target and target not in ("main", "vision", "light"):
            return target  # 直接指定了模型名
        # 最后回落
        if target == "light" and self._models.get("light"):
            return self._models["light"]
        if target == "vision" and self._models.get("vision"):
            return self._models["vision"]
        return self._models["main"]

    # ---- 修改（面板配置） ----
    def set_route(self, task_type: str, target: str) -> None:
        self._routing[task_type] = target

    # ---- 状态 ----
    def dump(self) -> dict:
        return {"models": dict(self._models), "routing": dict(self._routing)}


# 全局单例
router = ModelRouter()
