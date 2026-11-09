# services/llm_gateway.py
"""统一 LLM 出口（P1）

目的：把散落在 20+ 处的 `llm_client.chat(...)` 收到一个入口，便于：
- 按场景选默认模型/超时（profile）
- 统一打点（按调用方 tag 统计成功/失败/慢调用）
- 以后加熔断、降级、成本统计只改这里

用法（等价于原来的 llm_client.chat，参数完全兼容）：
    from services.llm_gateway import ask
    resp = ask(messages, profile="deep", temperature=0.3, tag="react")

profile:
    default  llm_client 默认模型，120s
    deep     router.route("main")，240s
    fast     router.route("light")，120s
    summary  router.route("summary")，60s
    vision   router.route("vision")，120s
    chat     不指定模型（调用方传），90s，num_ctx=12288
"""
import sys
import time

from core.llm_client import llm_client
from core.logger import root_logger
from core.metrics import metrics
from core.model_router import router

PROFILES = {
    "default": {"route": None, "timeout": 120.0},
    # num_ctx 16384：实测 7B Q4 在 12288 时占 5.2GB，16384 约 5.4GB（8G 卡可行）。
    # 项目固定注入部分约 4840 字符，4096 窗口会截断（实测 4900 字符→仅评估 3569）。
    "deep": {"route": "main", "timeout": 240.0, "num_ctx": 16384},
    "fast": {"route": "light", "timeout": 120.0},
    "summary": {"route": "summary", "timeout": 60.0},
    "vision": {"route": "vision", "timeout": 120.0},
    "chat": {"route": None, "timeout": 90.0, "num_ctx": 12288},
}


def ask(messages, profile=None, temperature=0.4, model=None, timeout=None,
        num_ctx=None, use_cache=False, tag=None, **kwargs):
    """统一 LLM 调用（参数与 llm_client.chat 兼容；profile 只补默认值）"""
    cfg = PROFILES.get(profile or "default", PROFILES["default"])
    if model is None and cfg.get("route"):
        model = router.route(cfg["route"])
    if timeout is None:
        timeout = cfg.get("timeout")
    if num_ctx is None:
        num_ctx = cfg.get("num_ctx")
    if tag is None:
        try:
            tag = sys._getframe(1).f_globals.get("__name__", "?").split(".")[-1]
        except Exception:
            tag = "?"
    t0 = time.time()
    resp = llm_client.chat(messages, temperature=temperature, model=model,
                           use_cache=use_cache, timeout=timeout,
                           num_ctx=num_ctx, **kwargs)
    dt = time.time() - t0
    try:
        metrics.count(f"llm_gw_{tag}")
        metrics.count(f"llm_gw_{tag}_ok" if resp.success else f"llm_gw_{tag}_fail")
    except Exception:
        pass
    if dt > 60:
        root_logger.warning(f"[LLM] {tag} 慢调用 {dt:.1f}s（profile={profile or 'default'}）")
    return resp


def ask_external(messages, temperature=0.9, timeout=None, model=None, tag="external"):
    """外部 OpenAI 兼容服务（如 DeepSeek），走 chat_openai"""
    t0 = time.time()
    resp = llm_client.chat_openai(messages, temperature=temperature,
                                  timeout=timeout, model=model)
    try:
        metrics.count(f"llm_gw_{tag}")
    except Exception:
        pass
    if time.time() - t0 > 60:
        root_logger.warning(f"[LLM] {tag} 慢调用 {time.time() - t0:.1f}s（external）")
    return resp
