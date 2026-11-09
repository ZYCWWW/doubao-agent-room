"""
Chroma向量库封装
拆分为4个独立集合：
episode_memory ｜情景记忆，带衰减归档
knowledge_fact ｜通用事实知识
knowledge_skill｜软件操作技能（版本、is_valid失效标记）
knowledge_rule ｜系统规则、权限约束
"""
import os
import threading
import time

# 嵌入模型已缓存在本机时强制离线：避免每次冷启动都向 HuggingFace 发 HEAD 请求
# （hf-mirror 偶发 502/429，最坏能把首轮检索拖几分钟）——R3
_HF_MODEL_CACHE = os.path.join(
    os.path.expanduser("~"), ".cache", "huggingface", "hub",
    "models--sentence-transformers--all-MiniLM-L6-v2")
if os.path.isdir(_HF_MODEL_CACHE):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import chromadb
from chromadb.utils import embedding_functions
from core.config_loader import config_loader
from core.logger import root_logger

PERSIST_PATH = config_loader["kb"]["persist_path"]

# chroma 多进程共用同一个 PersistentClient 时的瞬时错误：hnsw 段未落盘 / sqlite 锁
_RETRY_HINTS = ("hnsw", "segment", "nothing found on disk", "internal error",
                "database is locked", "unable to open database",
                # chromadb 1.5.x：多进程共用 PersistentClient 时 Rust 绑定偶发未就绪
                # 实测表现为 "'RustBindingsAPI' object has no attribute 'bindings'"，
                # 属瞬时错误，重试即可（此前不在白名单里 → 整轮知识注入被跳过）
                "has no attribute 'bindings'")


_DEFAULT_DELAYS = (0.0, 0.15, 0.45)


def _call_with_retry(fn, *args, delays=_DEFAULT_DELAYS, **kwargs):
    """对 chroma 的瞬时错误重试，非瞬时错误直接抛出。

    delays 可覆盖重试间隔：启动瞬间建 PersistentClient 要初始化 Rust 绑定，
    比 add/query 这类调用慢得多，默认 0.6s 总预算撑不住（见 CHANGELOG 56.4）。
    """
    last = None
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if not any(h in msg for h in _RETRY_HINTS):
                raise
            last = e
            root_logger.warning(f"chroma 瞬时错误（第{attempt + 1}次，将重试）：{str(e)[:120]}")
    raise last

# 嵌入模型懒加载：避免 import 时下载/加载 103 层权重拖慢启动（首次检索才初始化）
_ef = None
_ef_lock = threading.Lock()

# huggingface.co 本机常 502（网络不可达）：默认走国内镜像，避免每次新进程初始化嵌入模型卡 ~40s 重试
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _get_ef():
    global _ef
    if _ef is None:
        with _ef_lock:                      # 并发预热时只加载一次（避免多线程重复加载模型）
            if _ef is None:
                root_logger.info("初始化向量嵌入模型 all-MiniLM-L6-v2 (HF_ENDPOINT=%s) ...",
                                 os.environ.get("HF_ENDPOINT", "default"))
                _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2")
    return _ef


class ChromaKB:
    # 旧 4 集合：兼容保留（启动时可建）
    BASE_COLLECTIONS = ("episode_memory", "knowledge_fact", "knowledge_skill", "knowledge_rule")
    # 分区集合：由面板进程创建；读进程只「打开已存在的」，不创建（R14）
    EXTRA_COLLECTIONS = ("memory_core", "memory_cold", "memory_archive", "reflection")

    def __init__(self):
        # 2026-11-09：chromadb 1.5.9 首次 PersistentClient 要初始化 Rust 绑定，
        # 慢于 _call_with_retry 默认的 0.6s 预算，启动首轮必撞
        # "'RustBindingsAPI' object has no attribute 'bindings'"（CHANGELOG 56.4 遗留）。
        # 这一行此前没进重试，所以白名单加了串也没救到它；这里单独给足 4s。
        self.client = _call_with_retry(chromadb.PersistentClient, path=PERSIST_PATH,
                                       delays=(0.0, 1.0, 3.0))
        self.collections = {
            name: self.client.get_or_create_collection(name=name, embedding_function=_get_ef())
            for name in self.BASE_COLLECTIONS
        }
        for name in self.EXTRA_COLLECTIONS:
            try:
                # 只打开已存在的集合：get_collection 在不存在时抛错，不会创建
                self.collections[name] = self.client.get_collection(
                    name=name, embedding_function=_get_ef())
            except Exception:
                continue

    def add_document(self, coll_name: str, text: str, metadata: dict = None, doc_id: str = None):
        """新增向量文档，自动生成uuid id"""
        coll = self.collections[coll_name]
        if doc_id is None:
            import uuid
            doc_id = str(uuid.uuid4())
        _call_with_retry(coll.add, ids=[doc_id], documents=[text], metadatas=[metadata or {}])
        return doc_id

    def update_document(self, coll_name: str, doc_id: str, text: str, metadata: dict):
        """更新已有文档内容和元数据（用于技能版本升级、记忆归档标记）"""
        coll = self.collections[coll_name]
        _call_with_retry(coll.update, ids=[doc_id], documents=[text], metadatas=[metadata])

    def delete_document(self, coll_name: str, doc_id: str) -> bool:
        """按 id 删除一条向量文档（面板手动清理用）。
        删除是破坏性操作，所以只接受明确 id，不做按 metadata 批量删。"""
        coll = self.collections[coll_name]
        if not doc_id:
            return False
        _call_with_retry(coll.delete, ids=[doc_id])
        return True

    def refresh_collections(self) -> dict:
        """重新打开所有集合对象，丢弃本进程的索引快照。

        chromadb 多进程共用同一个 persist 目录时，每个进程的 Collection 对象各自持有
        一份索引：别的进程（主循环 / skill_extractor）写入后，本进程 query 看不到新
        条目，而 count() 又能从 sqlite 读到——表现为「条数显示 2，检索只命中 1」。
        面板在渲染检索区前调一次即可。"""
        for name in list(self.collections.keys()):
            try:
                self.collections[name] = self.client.get_collection(
                    name=name, embedding_function=_get_ef())
            except Exception as e:
                root_logger.warning(f"集合刷新跳过 {name}：{str(e)[:80]}")
        return self.collections

    def query(self, coll_name: str, query_text: str, top_k=4, filter=None):
        """向量检索，支持metadata过滤条件；空query禁止向量检索，改用全量读取"""
        coll = self.collections[coll_name]
        if not query_text or query_text.strip() == "":
            # 空查询：直接拿全部条目，不做向量相似度检索，避免chroma报错
            all_data = _call_with_retry(coll.get, limit=top_k, where=filter)
            out = []
            if not all_data or not all_data["ids"]:
                return out
            for i, doc in enumerate(all_data["documents"]):
                out.append({
                    "id": all_data["ids"][i],
                    "document": doc,
                    "metadata": all_data["metadatas"][i],
                    "distance": 0.0
                })
            return out
        res = _call_with_retry(coll.query, query_texts=[query_text],
                              n_results=top_k, where=filter)
        out = []
        if not res or not res["ids"] or len(res["ids"][0]) == 0:
            return out
        for i, doc in enumerate(res["documents"][0]):
            out.append({
                "id": res["ids"][0][i],
                "document": doc,
                "metadata": res["metadatas"][0][i],
                "distance": res["distances"][0][i]
            })
        return out

_chroma_instance = None


def get_chroma_kb() -> "ChromaKB":
    """惰性单例：首次访问才创建（创建时才加载向量嵌入模型）"""
    global _chroma_instance
    if _chroma_instance is None:
        _chroma_instance = ChromaKB()
    return _chroma_instance


class _LazyChromaProxy:
    """代理对象：保持 chroma_kb.xxx 调用语法，但把真实初始化推迟到首次访问"""
    def __getattr__(self, name):
        return getattr(get_chroma_kb(), name)


chroma_kb = _LazyChromaProxy()
