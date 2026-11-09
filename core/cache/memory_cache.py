# core/cache/memory_cache.py
"""
L1 内存缓存：TTL + LRU（无第三方依赖，OrderedDict 自实现）
- TTLCache：单层 key
- NamespacedCache：命名空间 key（embedding/schema/template/file）
"""
import time
import threading
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    def __init__(self, maxsize: int = 1024, ttl: float = 300.0):
        self._maxsize = max(1, maxsize)
        self._default_ttl = max(0.01, ttl)
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            _ts, value, expire = item
            if time.time() >= expire:
                del self._data[key]
                return default
            self._data.move_to_end(key)
            return value

    def put(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        expire = time.time() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            self._data[key] = (time.time(), value, expire)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def keys(self) -> list:
        with self._lock:
            return list(self._data.keys())


class NamespacedCache:
    """命名空间缓存：内部 key=(namespace, key)"""

    def __init__(self, maxsize: int = 2048, ttl: float = 300.0):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, namespace: str, key: Any, default: Any = None) -> Any:
        return self._cache.get((namespace, key), default)

    def put(self, namespace: str, key: Any, value: Any,
            ttl: Optional[float] = None) -> None:
        self._cache.put((namespace, key), value, ttl)

    def invalidate(self, namespace: str, key: Any = None) -> None:
        if key is None:
            # 清空整个命名空间
            ns = (namespace,)
            with self._cache._lock:
                for k in [k for k in self._cache._data if k[0] == namespace]:
                    del self._cache._data[k]
        else:
            self._cache.invalidate((namespace, key))

    def clear(self) -> None:
        self._cache.clear()

    def size(self) -> int:
        return self._cache.size()
