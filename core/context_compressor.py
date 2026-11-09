# core/context_compressor.py
"""
上下文压缩：预算分档 + 滚动摘要 + observation 压缩
- budget_for(task, tool_calls, rounds)：任务复杂度 → 预算（light≤4k / normal≤6k / deep≤10k）
- compress_messages：超预算 → 保留 system + 最近 N 轮原文，中间轮次滚动摘要（摘要结果进内存缓存）
- summarize_observation：超长 observation 确定性截断（保头尾、标省略，不再调模型重述）
"""
import threading
import time
from typing import Optional
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.model_router import router
from core.logger import root_logger

BUDGETS = {"light": 4000, "normal": 6000, "deep": 10000}
# 字符数 ≈ token 的粗估系数（中文1字≈1token，英文4字符≈1token，折中取1.5）
_CHAR_PER_TOKEN = 1.5


def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / _CHAR_PER_TOKEN))


class ContextCompressor:
    def __init__(self):
        self._summary_cache: dict[tuple, str] = {}
        self._lock = threading.RLock()
        self._max_cache = 256

    # ---- 预算 ----
    def budget_for(self, task: str, tool_calls: int = 0, rounds: int = 0) -> int:
        n = len(task or "")
        if n > 800 or tool_calls > 6 or rounds > 4:
            return BUDGETS["deep"]
        if n > 200 or tool_calls > 2:
            return BUDGETS["normal"]
        return BUDGETS["light"]

    # ---- 消息压缩 ----
    def compress_messages(self, messages: list, budget_tokens: int,
                          keep_recent: int = 4) -> list:
        """超预算 → 中间轮次合并为一条滚动摘要；system 与最近 keep_recent 轮保留原文"""
        if not messages:
            return messages
        total = sum(_estimate_tokens(m.get("content", "")) for m in messages)
        if total <= budget_tokens:
            return messages

        # 拆分：system(0) | 早期轮 | 最近 keep_recent 轮
        system = [messages[0]] if messages and messages[0].get("role") == "system" else []
        rest = messages[len(system):]
        if len(rest) <= keep_recent:
            return messages  # 不够压缩，保留

        early = rest[:-keep_recent]
        recent = rest[-keep_recent:]

        # 滚动摘要（带缓存：同内容同轮不重复摘要）
        early_text = "\n".join(
            f"[{m.get('role')}] {m.get('content', '')}" for m in early
        )
        key = ("summary", hash(early_text) & 0xFFFFFFFF)
        summary = self._get_summary(key)
        if summary is None:
            summary = self._rollup(early_text)
            self._put_summary(key, summary)

        return system + [{"role": "system", "content": f"（早期对话摘要）\n{summary}"}] + recent

    # ---- observation 压缩 ----
    def summarize_observation(self, text: str, max_len: int = 2000) -> str:
        """确定性截断：保留头 65%、尾 35%，中间标记省略，不调模型重述。

        为什么不用 LLM 摘要：摘要由 light 模型（llama3.1:8b）执行，而实测该模型
        稳定边界只有 2 步/5 实体，长列表必然漏关键项；且"重述"会改写数值与条目名，
        截断只搬运、不改写，丢了也能看出来丢了。
        """
        if len(text) <= max_len:
            return text
        head_len = int(max_len * 0.65)
        tail_len = max(0, max_len - head_len - 20)
        omitted = len(text) - head_len - tail_len
        return f"{text[:head_len]}\n…[省略 {omitted} 字符]…\n{text[-tail_len:]}"

    # ---- 摘要缓存 ----
    def _rollup(self, text: str) -> str:
        try:
            prompt = (
                "把下面的对话记录压缩成一段 300 字以内的摘要，"
                "保留任务目标、已用工具、关键结论、未解决问题：\n"
                f"{text[:8000]}"
            )
            resp = ask(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                model=router.route("summary"),
            )
            if resp.success and resp.content.strip():
                return resp.content.strip()[:800]
        except Exception as e:
            root_logger.warning(f"滚动摘要失败: {e}")
        return text[:400]

    def _get_summary(self, key: tuple) -> Optional[str]:
        with self._lock:
            return self._summary_cache.get(key)

    def _put_summary(self, key: tuple, value: str) -> None:
        with self._lock:
            if len(self._summary_cache) >= self._max_cache:
                self._summary_cache.clear()
            self._summary_cache[key] = value


# 全局单例
context_compressor = ContextCompressor()
