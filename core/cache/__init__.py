# core/cache/__init__.py
"""
三级缓存：
- L1 内存：memory_cache（embedding/schema/模板/沙盘文件，TTL+LRU）
- L2 磁盘：llm_cache（LLM 调用持久缓存）
- schema_cache：工具 schema 预编译缓存（精简系统 prompt）
"""
from core.cache.memory_cache import NamespacedCache, TTLCache
from core.cache.llm_cache import llm_cache
