# core/cache/llm_cache.py
"""
L2 磁盘缓存：LLM 调用持久化缓存
- key = sha256(model + messages + temperature)
- 每 key 一个 json 文件；写走统一文件锁；TTL 可配置（默认 24h）
- 命中即返回原文，未命中返回 None（调用方自行 store）
"""
import hashlib
import json
import os
import time
import threading
from pathlib import Path
from typing import Optional

from core.config_loader import PROJECT_ROOT
from core.file_lock import file_lock

LLM_CACHE_DIR = PROJECT_ROOT / "cache" / "llm_cache"


def _messages_str(messages: list) -> str:
    try:
        return json.dumps(messages, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(messages)


class LLMCache:
    def __init__(self, cache_dir: Optional[Path] = None,
                 ttl: float = 86400.0, enabled: bool = True):
        self._dir = Path(cache_dir) if cache_dir else LLM_CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl
        self._enabled = enabled
        self._hit = 0
        self._miss = 0
        self._lock = threading.RLock()

    # ---- key / path ----
    def _key(self, model: str, messages: list, temperature: float) -> str:
        raw = f"{model}|{temperature}|{_messages_str(messages)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    # ---- 读写 ----
    def get(self, model: str, messages: list, temperature: float) -> Optional[str]:
        if not self._enabled:
            return None
        key = self._key(model, messages, temperature)
        p = self._path(key)
        if not p.exists():
            with self._lock:
                self._miss += 1
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) > self._ttl:
                p.unlink(missing_ok=True)
                with self._lock:
                    self._miss += 1
                return None
            with self._lock:
                self._hit += 1
            return data.get("content")
        except Exception:
            with self._lock:
                self._miss += 1
            return None

    def store(self, model: str, messages: list, temperature: float,
              content: str) -> None:
        if not self._enabled or not content:
            return
        key = self._key(model, messages, temperature)
        p = self._path(key)
        payload = json.dumps(
            {"ts": time.time(), "model": model, "temperature": temperature,
             "content": content},
            ensure_ascii=False,
        )
        try:
            with file_lock(str(p), timeout=10.0):
                p.write_text(payload, encoding="utf-8")
        except Exception:
            pass  # 缓存写失败不影响主流程

    # ---- 维护 ----
    def cleanup(self, max_age: Optional[float] = None) -> int:
        """清理过期缓存文件，返回清理数量"""
        max_age = max_age or self._ttl
        removed = 0
        now = time.time()
        for p in self._dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if now - data.get("ts", 0) > max_age:
                    p.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                continue
        return removed

    def stats(self) -> dict:
        with self._lock:
            return {"hit": self._hit, "miss": self._miss,
                    "files": len(list(self._dir.glob("*.json")))}


# 全局单例
llm_cache = LLMCache()


def llm_cached(model: str, messages: list, temperature: float) -> Optional[str]:
    """便捷：命中返回内容，未命中返回 None"""
    return llm_cache.get(model, messages, temperature)


def llm_store(model: str, messages: list, temperature: float, content: str) -> None:
    llm_cache.store(model, messages, temperature, content)
