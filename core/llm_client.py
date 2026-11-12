"""
Ollama LLM客户端，封装chat对话接口，统一异常捕获
返回包装对象区分成功/失败
支持：多模型路由（model参数）、并发信号量（防打爆Ollama）、
     持久化缓存开关（use_cache）、耗时埋点（metrics）
"""
import requests
import json
import threading
import time
from core.config_loader import config_loader
from core.secrets import get_secret
from core.logger import root_logger
from core.metrics import metrics, timer
from core.cache.llm_cache import llm_cached, llm_store

class LLMResponse:
    """LLM返回结果包装类"""
    def __init__(self, success: bool, content: str):
        self.success = success
        self.content = content

class LLMClient:
    def __init__(self):
        # 读取用户配置（config/config.toml），未配置时回落默认值
        ollama_cfg = config_loader["ollama"]
        self.url = ollama_cfg["chat_url"]
        self.model = ollama_cfg["chat_model"]
        self.vision_model = ollama_cfg["vision_model"]
        self.timeout = ollama_cfg["timeout"]
        # 并发信号量：限制同时打向 LLM 服务的请求数
        max_concurrent = ollama_cfg.get("max_concurrent", 2)
        self._sem = threading.Semaphore(max(1, int(max_concurrent)))
        # 外部模型（OpenAI 兼容服务，如 DeepSeek 官方 API）配置
        ext_cfg = config_loader.get("external_llm", {})
        self.ext_enabled = bool(ext_cfg.get("enabled"))
        self.ext_base_url = (ext_cfg.get("base_url") or "").strip().rstrip("/")
        # 密钥不再从 config.toml 明文读：环境变量 / config/secrets.toml（R10）
        self.ext_api_key = get_secret("external_llm", "api_key")
        self.ext_model = ext_cfg.get("model") or "deepseek-chat"
        self.ext_timeout = float(ext_cfg.get("timeout", 120))

    def chat(self, messages: list, temperature=0.4, model: str = None,
             use_cache: bool = False, timeout: float = None, num_ctx: int = None):
        """
        调用ollama chat接口
        :param messages: 消息列表 [{"role":"user","content":"xxx"}]
        :param temperature: 温度，越低越确定，越高越发散
        :param model: 指定模型名；None 时使用默认主模型
        :param use_cache: 是否启用 L2 持久化缓存（确定性调用建议开启）
        :param timeout: 单次请求超时（秒），None 时用配置默认值（思考型任务可传更长）
        :param num_ctx: 上下文窗口 token 数（None 保持 Ollama 默认，避免影响教学等既有调用）
        :return: LLMResponse
        """
        model = model or self.model
        timeout = timeout or self.timeout
        if use_cache:
            cached = llm_cached(model, messages, temperature)
            if cached is not None:
                metrics.count("llm_cache_hit")
                return LLMResponse(True, cached)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature
        }
        if num_ctx:
            payload["options"] = {"num_ctx": num_ctx}
        # 思考型模型（qwen3.5 / deepseekR1）默认把正文留在 thinking，message.content 为空；
        # 关闭思维链，保证 content 有正文（旧版 Ollama 会忽略该字段）
        payload["think"] = False
        with self._sem:
            # 轻量重试：Ollama 本地偶发连接重置/超时/5xx 时自动重试（404 模型不存在不重试）
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    with timer("llm_chat"):
                        data = self._post_json(payload, timeout)
                    content = (data.get("message") or {}).get("content") or ""
                    if not content.strip():
                        # 思考型模型：正文可能落在 thinking 字段，回退取用，避免群聊里"永远不开口"
                        content = (data.get("message") or {}).get("thinking") or ""
                    if use_cache:
                        llm_store(model, messages, temperature, content)
                    # 2026-11-09 实测：8GB 显存装不下三个模型，api/ps 显示"已加载"也可能一半在 CPU，
                    # 每次调用先花几十秒换入（llama3.1:8b 实测 load 85.7s / 总 85.9s）。
                    # 把加载占比写进日志，下次一眼区分「换入慢」和「算得慢」。
                    try:
                        _ld = float(data.get("load_duration") or 0)
                        _td = float(data.get("total_duration") or 0)
                        if _td > 0 and _ld / _td > 0.4:
                            root_logger.warning(
                                f"[LLM] {model} 加载占 {_ld/1e9:.1f}s/{_td/1e9:.1f}s"
                                f"（显存不足需换入），推理仅 {data.get('eval_count') or 0} tokens")
                    except Exception:
                        pass
                    return LLMResponse(True, content)
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else 0
                    if status == 404 or attempt >= max_attempts:
                        root_logger.error(f"❌ LLM请求失败: {e}")
                        return LLMResponse(False, str(e))
                    root_logger.warning(f"LLM请求 {status} 重试 {attempt}/{max_attempts}")
                    metrics.count("llm_retry")
                    time.sleep(0.5 * attempt)
                except requests.exceptions.RequestException as e:
                    # 读超时多半是模型在换入显存，不是偶发抖动：同一状态再重试 3 次只是白等 240s
                    # （实测七步第 2 步 3 次 120s 全超时 = 367.8s），所以超时只补 1 次；
                    # 连接重置 / 5xx 仍按原上限重试。
                    is_timeout = isinstance(e, (requests.exceptions.Timeout,
                                                requests.exceptions.ReadTimeout))
                    limit = 2 if is_timeout else max_attempts
                    if attempt >= limit:
                        if is_timeout:
                            root_logger.error(
                                f"❌ LLM请求失败: {e}（读超时；若反复出现，说明该模型在换入显存，"
                                f"请检查 GPU 同时驻留了几个模型）")
                        else:
                            root_logger.error(f"❌ LLM请求失败: {e}")
                        return LLMResponse(False, str(e))
                    root_logger.warning(f"LLM请求异常 重试 {attempt}/{limit}: {e}")
                    metrics.count("llm_retry")
                    time.sleep(0.5 * attempt)

    def _post_json(self, payload, timeout):
        """POST 并解析 JSON，带墙钟总时长上限。

        为什么不只靠 requests 的 socket 超时：
        Ollama 在生成期会持续往连接里写空 chunk，socket 读超时（两块数据之间）
        因此被反复重置，永远触发不了。2026-11-12 实测出现过单次调用挂
        13281.6s（3 小时 41 分），期间没有任何重试日志——正是这个机制。
        这里另加一道墙钟：从发出请求算起超过 timeout*2 就主动断开。
        """
        t0 = time.time()
        deadline = float(timeout) * 2.0
        resp = requests.post(self.url, json=payload, timeout=timeout, stream=True)
        try:
            resp.raise_for_status()
            buf = []
            for chunk in resp.iter_content(chunk_size=4096):
                if time.time() - t0 > deadline:
                    raise requests.exceptions.ReadTimeout(
                        f"总时长超过 {deadline:.0f}s（墙钟兜底，Ollama 生成期心跳会重置 socket 超时），已主动断开")
                if chunk:
                    buf.append(chunk)
            return json.loads(b"".join(buf))
        finally:
            resp.close()

    def chat_openai(self, messages: list, temperature=0.9, model: str = None,
                    timeout: float = None) -> LLMResponse:
        """外部 OpenAI 兼容服务（DeepSeek 官方 API / OpenAI / LM Studio 等）：
        POST {base_url}/chat/completions，Bearer 鉴权。
        :param messages: [{role: system|user|assistant, content}]（与 Ollama 格式一致）
        :return: LLMResponse；未配置/未启用/鉴权失败 → success=False"""
        if not self.ext_enabled:
            return LLMResponse(False, "external_llm disabled")
        if not self.ext_base_url or not self.ext_api_key:
            return LLMResponse(False, "external_llm not configured (base_url/api_key)")
        model = model or self.ext_model
        timeout = timeout or self.ext_timeout
        url = f"{self.ext_base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
        headers = {"Authorization": f"Bearer {self.ext_api_key}", "Content-Type": "application/json"}
        with self._sem:
            for attempt in range(1, 4):
                try:
                    with timer("llm_chat_ext"):
                        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                        resp.raise_for_status()
                        data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return LLMResponse(True, content)
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else 0
                    if status in (401, 403):
                        root_logger.error(f"❌ 外部LLM鉴权失败({status}): 请检查 api_key/base_url")
                        return LLMResponse(False, f"auth error {status}")
                    if attempt >= 3:
                        root_logger.error(f"❌ 外部LLM请求失败: {e}")
                        return LLMResponse(False, str(e))
                    root_logger.warning(f"外部LLM {status} 重试 {attempt}/3")
                    metrics.count("llm_retry")
                    time.sleep(0.5 * attempt)
                except requests.exceptions.RequestException as e:
                    if attempt >= 3:
                        root_logger.error(f"❌ 外部LLM请求失败: {e}")
                        return LLMResponse(False, str(e))
                    root_logger.warning(f"外部LLM请求异常 重试 {attempt}/3: {e}")
                    metrics.count("llm_retry")
                    time.sleep(0.5 * attempt)

# 全局单例
llm_client = LLMClient()
