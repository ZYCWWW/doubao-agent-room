"""
情景记忆服务：新增记忆、定时扫描归档
episode_memory 集合专用（旧接口兼容）
+ P2：四层分区（memory_core/cold/archive/reflection）、TF-RI 打分、访问计数、
      自适应注意力检索、记忆-沙盘双向溯源、反思记忆落库
"""
import time
import uuid
from core.chroma_kb import chroma_kb, _get_ef
from core.memory_scorer import calculate_score, need_archive, SCORE_SAVE, SCORE_ARCHIVE
from core.memory.tfri import tfri_score, update_access_metadata
from core.memory.attention import rerank, partition_budget, TYPE_GAIN
from core.memory.link import link_memory_to_sandbox, add_sandbox_backlink
from core.memory.reflection import REFLECTION_COLL
from core.logger import root_logger

# 分区集合名
PARTITIONS = ("memory_core", "memory_cold", "memory_archive", REFLECTION_COLL)

# 访问计数节流：id -> 上次写回时间（每进程每条目每小时最多一次）
_ACCESS_TOUCH = {}

# 过滤清洗函数
def clean_metadata(meta: dict) -> dict:
    allowed_types = (str, int, float, bool)
    out = {}
    for k, v in meta.items():
        if isinstance(v, allowed_types):
            out[k] = v
        elif v is None:
            out[k] = "null"
        else:
            out[k] = str(v)
    return out


def _ensure_collections():
    """确保 P2 分区集合存在（**写侧/启动专用**：读路径不要调用，避免多进程 get_or_create 竞争）"""
    for coll in PARTITIONS:
        if coll not in chroma_kb.collections:
            chroma_kb.collections[coll] = chroma_kb.client.get_or_create_collection(
                name=coll, embedding_function=_get_ef())


class MemoryService:
    def ensure_collections(self) -> None:
        """分区集合预热（写侧/启动专用；读路径不要调用）"""
        _ensure_collections()

    def add_episode_memory(self, session_id: str, text: str, base_score: float, sandbox_ref: str = None):
        """添加一条情景记忆：**写入分区**（R4 写入侧迁移），新记忆进 core 热区。
        :param base_score: LLM 评估的初始分数（>=6 才会保存）
        :param sandbox_ref: 关联沙盘证据文件路径，用于溯源
        """
        return self.add_memory(text, importance=float(base_score), layer="core",
                               sandbox_ref=sandbox_ref, mtype="episode",
                               session_id=session_id)

    # ---------- R4：旧集合迁移 + 分区下沉 ----------

    def migrate_legacy(self, limit: int = 500) -> dict:
        """把旧 episode_memory 集合迁入分区（幂等：迁过的旧条目标记 migrated=True）。
        旧集合保留只读，不再作为写入/归档目标。"""
        if "episode_memory" not in chroma_kb.collections:
            return {"moved": 0, "skipped": 0}
        _ensure_collections()
        moved = skipped = 0
        for item in chroma_kb.query("episode_memory", "", top_k=limit):
            meta = dict(item.get("metadata") or {})
            if meta.get("migrated"):
                skipped += 1
                continue
            try:
                base = float(meta.get("base_score") or 5.0)
            except (TypeError, ValueError):
                base = 5.0
            layer = "core" if base >= SCORE_SAVE else ("cold" if base >= SCORE_ARCHIVE else "archive")
            coll = {"core": "memory_core", "cold": "memory_cold", "archive": "memory_archive"}[layer]
            new_meta = {"base_score": base,
                        "created_ts": float(meta.get("created_ts") or time.time()),
                        "access_count": int(meta.get("access_count") or 0),
                        "is_permanent": bool(meta.get("is_permanent")),
                        "type": str(meta.get("type") or "episode"),
                        "layer": layer,
                        "migrated_from": "episode_memory"}
            if meta.get("session_id"):
                new_meta["session_id"] = str(meta["session_id"])
            if meta.get("related_sandbox_ref"):
                new_meta["related_sandbox_ref"] = str(meta["related_sandbox_ref"])
            try:
                chroma_kb.collections[coll].add(ids=[item["id"]], documents=[item["document"]],
                                                metadatas=[clean_metadata(new_meta)])
                moved += 1
            except Exception as e:
                root_logger.warning(f"迁移记忆失败 id={item['id']}: {e}")
                continue
            meta["migrated"] = True
            meta["migrated_to"] = coll
            try:
                chroma_kb.update_document("episode_memory", item["id"], item["document"],
                                          clean_metadata(meta))
            except Exception:
                pass
        if moved:
            root_logger.info(f"[记忆迁移] episode_memory → 分区：{moved} 条")
        return {"moved": moved, "skipped": skipped}

    def _move(self, src: str, dst: str, item: dict) -> bool:
        """分区之间移动一条记忆（保持 id / 文本，metadata 更新 layer）"""
        if src not in chroma_kb.collections or dst not in chroma_kb.collections:
            return False
        layer = {"memory_core": "core", "memory_cold": "cold",
                 "memory_archive": "archive"}.get(dst, "cold")
        meta = clean_metadata(dict(item.get("metadata") or {}))
        meta["layer"] = layer
        meta["last_move_ts"] = time.time()
        if dst == "memory_archive":
            meta["archived"] = True
        try:
            chroma_kb.collections[dst].add(ids=[item["id"]], documents=[item["document"]],
                                           metadatas=[meta])
        except Exception as e:
            root_logger.warning(f"记忆下沉写入失败 {dst} id={item['id']}: {e}")
            return False
        try:
            chroma_kb.collections[src].delete(ids=[item["id"]])
        except Exception as e:
            root_logger.warning(f"记忆下沉删除源失败 {src} id={item['id']}: {e}")
        return True

    # ---------- P2 分区记忆 ----------

    def add_memory(self, text: str, importance: float = 6.0,
                   layer: str = "core", sandbox_ref: str = None,
                   permanent: bool = False, mtype: str = "episode",
                   session_id: str = None, doc_id: str = None) -> str:
        """
        写入分区记忆（memory_core / memory_cold / memory_archive / reflection）
        :param importance: 初始重要性（>=save 阈值才会存）
        :param layer: core | cold | archive | reflection
        :param sandbox_ref: 沙盘证据文件路径（双向溯源）
        :param permanent: 永久记忆（不衰减）
        :param mtype: 记忆类型（fact/rule/skill/episode/reflection）
        :return: 记忆 id
        """
        _ensure_collections()
        if layer not in PARTITIONS:
            layer = "core"
        coll = "memory_core" if layer == "core" else (
            "memory_cold" if layer == "cold" else (
                "memory_archive" if layer == "archive" else REFLECTION_COLL))
        ts = time.time()
        meta = {
            "session_id": session_id or "system",
            "base_score": float(importance),
            "created_ts": ts,
            "access_count": 0,
            "is_permanent": bool(permanent),
            "type": mtype,
            "layer": layer,
        }
        if sandbox_ref:
            meta["related_sandbox_ref"] = sandbox_ref
        meta = clean_metadata(meta)
        if doc_id is None:
            doc_id = str(uuid.uuid4())
        chroma_kb.add_document(coll, text, metadata=meta, doc_id=doc_id)
        # 沙盘侧溯源
        if sandbox_ref:
            add_sandbox_backlink(sandbox_ref, doc_id)
        root_logger.info(f"记忆写入 layer={layer} id={doc_id} importance={importance}")
        return doc_id

    def record_access(self, doc_id: str, coll: str = None, layer: str = None) -> bool:
        """访问计数 +1（TF-RI 增益）；按条目实际所在分区写，失败静默返回 False"""
        layer_coll = {"core": "memory_core", "cold": "memory_cold",
                      "archive": "memory_archive", "reflection": REFLECTION_COLL}
        tried = [coll] if coll else []
        if layer:
            tried.append(layer_coll.get(layer, "memory_core"))
        tried += [c for c in layer_coll.values() if c not in tried]
        for c in tried:
            if not c or c not in chroma_kb.collections:
                continue
            try:
                got = chroma_kb.collections[c].get(ids=[doc_id])
                if not got or not got["ids"]:
                    continue
                text = got["documents"][0]
                meta = got["metadatas"][0] or {}
                chroma_kb.update_document(c, doc_id, text,
                                          clean_metadata(update_access_metadata(meta)))
                return True
            except Exception as e:
                root_logger.warning(f"记忆访问计数更新失败 id={doc_id} coll={c}: {e}")
        return False

    def note_hits(self, hits: list, ttl: float = 3600.0) -> int:
        """检索命中后记一次访问（TF-RI 增益）：每进程每个条目每小时最多写一次，失败静默"""
        now = time.time()
        touched = 0
        for h in hits or []:
            hid = h.get("id")
            if not hid:
                continue
            if now - _ACCESS_TOUCH.get(hid, 0) < ttl:
                continue
            _ACCESS_TOUCH[hid] = now
            if self.record_access(hid, layer=(h.get("metadata") or {}).get("layer", "core")):
                touched += 1
        return touched

    def search_memory(self, query: str, top_k: int = 8,
                      layers: tuple = ("core", "cold"), budget: int = None) -> list:
        """
        分区注意力检索：core 50% / cold 30% / archive 不默认注入
        返回按注意力 rank 排序的结果（含 layer 字段）。
        **只读**：不再懒建集合（建集合是写操作，会和别的进程抢 hnsw 段）。
        """
        budget = budget if budget is not None else top_k
        parts = partition_budget(budget)
        out = []
        for layer in layers:
            coll = "memory_core" if layer == "core" else (
                "memory_cold" if layer == "cold" else (
                    "memory_archive" if layer == "archive" else REFLECTION_COLL))
            if coll not in chroma_kb.collections:
                continue
            n = parts.get(layer, parts.get("core", 2))
            try:
                res = chroma_kb.query(coll, query, top_k=max(1, n))
            except Exception as e:
                root_logger.warning(f"记忆检索失败 {coll}: {e}")
                continue
            for it in res:
                it = dict(it)
                meta = dict(it.get("metadata") or {})
                meta["layer"] = layer
                it["metadata"] = meta
                out.append(it)
        ranked = rerank(out, top_k=top_k)
        try:
            self.note_hits(ranked)   # 命中即记访问（TF-RI 增益不再恒 0）
        except Exception:
            pass
        return ranked

    def add_reflection(self, topic: str, scenario: str, lesson: str,
                       source: str, kind: str = "lesson") -> str:
        """反思记忆落库（reflection 分区）"""
        from core.memory.reflection import build_reflection
        ref = build_reflection(topic, scenario, lesson, source, kind)
        if ref is None:
            return None
        return self.add_memory(ref.to_text(), importance=ref.to_metadata()["base_score"],
                               layer="reflection", mtype="reflection",
                               permanent=False, doc_id=str(uuid.uuid4()))

    def link_sandbox(self, memory_id: str, sandbox_path: str) -> None:
        """记忆-沙盘双向溯源（记忆侧引用 + 沙盘侧 backlink）"""
        link_memory_to_sandbox(memory_id, sandbox_path)
        add_sandbox_backlink(sandbox_path, memory_id)

    def scan_and_archive(self):
        """遍历分区记忆：重算 TF-RI，core → cold → archive 分层下沉（R4）。
        旧 episode_memory 先做一次幂等迁移，不再作为写入/归档目标。"""
        mig = self.migrate_legacy()
        moved = 0
        plan = (("memory_core", "memory_cold", SCORE_SAVE),
                ("memory_cold", "memory_archive", SCORE_ARCHIVE))
        for src, dst, threshold in plan:
            if src not in chroma_kb.collections:
                continue
            for item in chroma_kb.query(src, "", top_k=999):
                meta = item.get("metadata") or {}
                if meta.get("is_permanent"):
                    continue
                try:
                    score = tfri_score(float(meta.get("base_score") or 5.0),
                                       int(meta.get("access_count") or 0),
                                       float(meta.get("created_ts") or time.time()),
                                       permanent=False)
                except (TypeError, ValueError):
                    root_logger.warning(f"记忆分数解析失败，跳过 id={item.get('id')}")
                    continue
                if score < threshold and self._move(src, dst, item):
                    moved += 1
                    root_logger.info(f"记忆下沉 {src}→{dst} id={item['id']} score={score:.2f}")
        if moved or mig.get("moved"):
            root_logger.info(f"记忆维护完成：迁移 {mig.get('moved', 0)} 条，下沉 {moved} 条")
        return {"migrated": mig.get("moved", 0), "moved_down": moved}


memory_service = MemoryService()
