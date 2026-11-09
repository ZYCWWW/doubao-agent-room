# core/cache/schema_cache.py
"""
工具 schema 预编译缓存：SYSTEM_PROMPT 的工具描述片段
- registry 更新时 invalidate；react_loop 每次构建 prompt 命中，避免重复拼装
- L1 内存缓存（TTL 10min）
"""
from typing import Optional
from core.cache.memory_cache import TTLCache

_schema_cache = TTLCache(maxsize=64, ttl=600.0)


def get_schema(key: str) -> Optional[str]:
    return _schema_cache.get(key)


def put_schema(key: str, text: str) -> None:
    _schema_cache.put(key, text)


def invalidate_schema(key: str = None) -> None:
    if key is None:
        _schema_cache.clear()
    else:
        _schema_cache.invalidate(key)


def build_or_get_schema(key: str, builder) -> str:
    """命中返回缓存；未命中调用 builder 并缓存"""
    cached = get_schema(key)
    if cached is not None:
        return cached
    text = builder()
    put_schema(key, text)
    return text
