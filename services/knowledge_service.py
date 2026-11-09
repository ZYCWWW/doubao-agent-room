"""
知识库服务：统一封装事实、技能、规则的增改查
P2：入库前冲突检测（一致/互补/冲突）+ 仲裁策略
"""
import json
import re
import time
import uuid
from core.chroma_kb import chroma_kb
from core.config_loader import PROJECT_ROOT
from core.logger import root_logger

# 待仲裁队列（冲突/互补但未自动入库的条目）
PENDING_FILE = PROJECT_ROOT / "sandbox" / "knowledge_pending.json"

# 冲突级别
CONFLICT_OK = "consistent"          # 一致（重复信息，可跳过）
CONFLICT_COMPLEMENT = "complementary"  # 互补（不同维度，均可保留）
CONFLICT_CONFLICT = "conflict"      # 冲突（需仲裁）

# 热度门槛：heat 低于此值算「冷门」。ranked_search(cold_reserve=True) 会保证
# top_k 里至少留一个冷门名额——热度是正反馈，不给保底会把冷门方向彻底饿死。
COLD_HEAT = 3

# 老师（豆包）从网上查证后讲给模型们的知识，来源统一标这个。
# 该来源享有一项与其他来源不同的待遇：top_k 里至少给它保留一个名额（老师保底），
# 否则它会被「热度已经封顶的无关条目」长期挡在注入之外，等于没讲过。
TEACHER_SOURCE = "teacher_web"


def _sim_from_distance(distance) -> float:
    """chroma 距离转相似度（0~1）"""
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, 1.0 - d / 2.0))


def _compare_texts(new_text: str, old_text: str) -> tuple:
    """
    比较两条知识文本，返回 (类别, 说明)
    优先级：矛盾断言 > 重复 > 互补
    """
    # 1. 矛盾断言检测（复用想象空间 validator 的断言比较）
    from core.imagination.validator import _compare_assertions
    kind, detail = _compare_assertions(
        {"text": new_text}, {"text": old_text})
    if kind:
        return CONFLICT_CONFLICT, f"断言矛盾：{detail}"

    # 2. 归一化去重判断
    def norm(t):
        return re.sub(r"[\s，。、；：！？（）()\"']", "", str(t or "")).lower()
    if norm(new_text) == norm(old_text):
        return CONFLICT_OK, "内容重复"
    if norm(new_text) in norm(old_text) or norm(old_text) in norm(new_text):
        return CONFLICT_OK, "内容包含重复"
    return CONFLICT_COMPLEMENT, "主题相关，信息互补"


class KnowledgeService:
    def add_knowledge(self, text: str, metadata: dict = None, coll="knowledge_fact"):
        """新增知识，默认存入事实库"""
        return chroma_kb.add_document(coll, text, metadata=metadata)

    def update_skill(self, doc_id: str, text: str, metadata: dict):
        """更新技能文档（版本迭代）"""
        chroma_kb.update_document("knowledge_skill", doc_id, text, metadata)

    def search_knowledge(self, query: str, top_k=4, filter_meta=None, coll="knowledge_fact"):
        """通用检索接口"""
        return chroma_kb.query(coll, query, top_k=top_k, filter=filter_meta)

    def search_skill(self, query: str, software_name: str = None, top_k=3):
        """检索有效技能，默认过滤is_valid=false的旧版本"""
        filter_dict = {
            "$and": [
                {"type": {"$eq": "skill"}},
                {"is_valid": {"$eq": True}}
            ]
        }
        if software_name:
            filter_dict["software"] = software_name
        return self.search_knowledge(query, top_k=top_k, filter_meta=filter_dict, coll="knowledge_skill")

    # ---------- 加权注入（注入排序只有这一处实现） ----------
    # 来源权重：effective = distance * weight，越小越优先被注入。
    # <1 表示该来源「优势更大」：验证过的结论比零散知识更容易被选中；
    # None 表示该来源默认禁用（调用方仍可用 exclude_sources 显式再排除）。
    SOURCE_WEIGHT = {
        "conflict_resolved": 0.75,     # 冲突仲裁后采纳的知识
        "teacher_web": 0.55,           # 老师从网上查证后讲给他们的知识（另有 top_k 保底）
        # 说明：weight 写 None 表示该来源默认禁用。react_final（工作任务结论）
        # 在闲聊里是噪音、在主循环里是有效参考，所以不在这里禁用，
        # 由 chat 调用方显式 exclude_sources=("react_final",)。
        # hypothesis_verified 项随猜想库于 2026-11-08 移除（该来源不再产生）。
    }

    # 热度因子：heat 越高查得越靠前（rank 更小）。heat_factor = 1/(1+heat*step)。
    # heat=0 → 1.0 / heat=10 → 0.667 / heat=40 → 0.333（封顶，防止热者恒热）。
    HEAT_CAP = 40
    HEAT_STEP = 0.05

    def ranked_search(self, query: str, top_k: int = 3, coll: str = "knowledge_fact",
                      threshold: float = None, exclude_sources=(), weights=None,
                      cold_reserve: bool = False) -> list:
        """按来源权重 + 热度重排，并保证来源多样性 / 冷门保底。
        :param threshold: 距离上限；None 表示不限（主循环召回沿用原行为）
        :param exclude_sources: 额外排除的来源
        :param weights: 自定义权重表，None 用 SOURCE_WEIGHT
        :param cold_reserve: True 时 top_k 内若无冷门条目，把末位让给候选池里最近的冷门。
                             只在「有引导语配套」的调用方开启（闲聊），主循环不开。
        :return: [{id, text, distance, source, weight, heat, meta, rank}]，rank 升序
        排序分 rank = distance * weight * heat_factor，三项都是「越小越优先」。
        多样性：前 top_k 全是同一来源时，把末位换成不同来源的最佳项，
        避免「一条内容的距离一直最近」就垄断全部注入名额。
        """
        wmap = self.SOURCE_WEIGHT if weights is None else weights
        docs = self.search_knowledge(query, top_k=max(top_k * 3, 8), coll=coll) or []
        picked = []
        for d in docs:
            text = (d.get("document") or "").strip()
            if not text:
                continue
            meta = d.get("metadata") or {}
            src = str(meta.get("source") or "")
            if src in exclude_sources:
                continue
            try:
                dist = float(d.get("distance", 9.9))
            except (TypeError, ValueError):
                dist = 9.9
            if threshold is not None and dist >= threshold:
                continue
            w = wmap.get(src, 1.0) if isinstance(wmap, dict) else 1.0
            if w is None:
                continue
            try:
                heat = int(float(meta.get("heat") or 0))
            except (TypeError, ValueError):
                heat = 0
            # 老师刚讲的知识不参与历史热度衰减：否则一次课堂内容会被
            # 「被反复注入过、热度已封顶」的无关条目长期挤在 top_k 之外
            heat_factor = 1.0 if src == "teacher_web" else (
                1.0 / (1.0 + min(heat, self.HEAT_CAP) * self.HEAT_STEP))
            picked.append({
                "id": d.get("id"), "text": text, "distance": dist, "source": src,
                "weight": w, "heat": heat,
                # chroma metadata 只接受 str/int/float/bool，嵌套值写回会报错，先滤掉
                "meta": {k: v for k, v in meta.items()
                         if isinstance(v, (str, int, float, bool))},
                "rank": dist * w * heat_factor,
            })
        picked.sort(key=lambda x: x["rank"])
        if len(picked) <= top_k:
            return picked
        head = picked[:top_k]
        # 多样性：前 top_k 全同源时，末位换成异源最佳项
        if top_k > 1 and len({p["source"] for p in head}) == 1:
            for alt in picked[top_k:]:
                if alt["source"] != head[0]["source"]:
                    head[-1] = alt
                    break
        # 冷门保底：top_k 内一个冷门都没有时，末位换成候选池里最近的冷门。
        # 只在 cold_reserve=True（闲聊）开启——保底会挤掉一条相关内容，
        # 必须有「低热度不等于不重要」的引导语配套，否则不如不塞。
        if cold_reserve and top_k > 1 and not any(
                p["heat"] < COLD_HEAT for p in head):
            for alt in picked[top_k:]:
                if alt["heat"] < COLD_HEAT:
                    head[-1] = alt
                    break
        # 老师保底：top_k 内一条老师讲过的都没有时，末位换成候选池里最近的那条。
        # 老师是专门讲的，没传到等于没讲；这条排在冷门保底之后，优先级更高，
        # 避免刚教的内容被「热度已封顶的无关条目」长期挡在注入之外。
        if top_k > 1 and not any(p["source"] == TEACHER_SOURCE for p in head):
            for alt in picked[top_k:]:
                if alt["source"] == TEACHER_SOURCE:
                    head[-1] = alt
                    break
        return head

    def bump_heat(self, pick: dict, coll: str = "knowledge_fact") -> bool:
        """被注入一次就记一次热度（写回 chroma metadata）。
        刻意只允许调用方对「本次真正注入的」条目调用，避免全量检索把热度刷高。
        失败静默返回 False：热度是锦上添花，不该阻塞聊天。"""
        doc_id = (pick or {}).get("id")
        if not doc_id:
            return False
        try:
            meta = dict((pick or {}).get("meta") or {})
            meta["heat"] = int(meta.get("heat") or 0) + 1
            chroma_kb.update_document(coll, doc_id, pick.get("text") or "", meta)
            return True
        except Exception as e:
            root_logger.warning(f"热度写回失败 {doc_id}: {str(e)[:80]}")
            return False

    # ---------- P2 冲突检测与仲裁 ----------

    def check_conflict(self, text: str, coll="knowledge_fact", top_k=3) -> dict:
        """
        入库前冲突检测：检索同主题既有知识，逐条比对
        :return: {status, items: [{doc_id, status, detail, document}]}
          status: consistent | complementary | conflict
        """
        hits = self.search_knowledge(text, top_k=top_k, coll=coll)
        items = []
        for h in hits:
            old = h.get("document", "")
            sim = _sim_from_distance(h.get("distance"))
            status, detail = _compare_texts(text, old)
            items.append({
                "doc_id": h.get("id"),
                "status": status,
                "detail": detail,
                "document": old,
                "similarity": round(sim, 3),
            })
        if not items:
            return {"status": CONFLICT_OK, "items": []}
        # 汇总级别：冲突优先，其次互补
        statuses = {it["status"] for it in items}
        if CONFLICT_CONFLICT in statuses:
            status = CONFLICT_CONFLICT
        elif CONFLICT_COMPLEMENT in statuses:
            status = CONFLICT_COMPLEMENT
        else:
            status = CONFLICT_OK
        return {"status": status, "items": items}

    def add_knowledge_safe(self, text: str, metadata: dict = None,
                           coll="knowledge_fact", auto_resolve=False) -> dict:
        """
        带冲突检测的入库
        :param auto_resolve: True=互补/一致自动入库，冲突跳过；False=一律先检查返回结果
        :return: {action, doc_id, status, conflict_detail}
          action: added | skipped | needs_review
        """
        check = self.check_conflict(text, coll=coll)
        status = check["status"]
        if status == CONFLICT_CONFLICT:
            detail = "；".join(f"与既有知识[{it['doc_id']}]：{it['detail']}"
                               for it in check["items"] if it["status"] == CONFLICT_CONFLICT)
            self.queue_pending(text, metadata, "conflict", detail)
            if auto_resolve:
                root_logger.info(f"知识入库冲突自动跳过：{detail}")
                return {"action": "skipped", "doc_id": None, "status": status,
                        "conflict_detail": detail}
            return {"action": "needs_review", "doc_id": None, "status": status,
                    "conflict_detail": detail}
        if status == CONFLICT_COMPLEMENT and not auto_resolve:
            # 互补：默认仍需人工确认（保守），但记录候选
            detail = "；".join(f"与[{it['doc_id']}]互补：{it['detail']}" for it in check["items"])
            self.queue_pending(text, metadata, "complement", detail)
            return {"action": "needs_review", "doc_id": None, "status": status,
                    "conflict_detail": detail}
        # 一致或（互补且 auto_resolve）→ 入库
        doc_id = self.add_knowledge(text, metadata=metadata, coll=coll)
        return {"action": "added", "doc_id": doc_id, "status": status,
                "conflict_detail": ""}

    def resolve_conflict(self, text: str, keep_new: bool, coll="knowledge_fact",
                         doc_id: str = None) -> dict:
        """人工/自动仲裁冲突：采纳新知识（可替换旧条目）或丢弃"""
        if keep_new:
            if doc_id:
                try:
                    chroma_kb.update_document(coll, doc_id, text,
                                              {"resolved_ts": time.time(),
                                               "replaced_by": "new"})
                    return {"action": "replaced", "doc_id": doc_id}
                except Exception as e:
                    root_logger.warning(f"冲突仲裁替换失败：{e}")
            new_id = self.add_knowledge(text, coll=coll,
                                          metadata={"source": "conflict_resolved"})
            return {"action": "kept_new", "doc_id": new_id}
        return {"action": "kept_old", "doc_id": None}

    # ---------- 待仲裁队列（面板出口） ----------

    def _load_pending(self) -> list:
        try:
            with open(PENDING_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_pending(self, items: list) -> None:
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    def queue_pending(self, text: str, metadata: dict = None, status: str = "conflict",
                      detail: str = "") -> dict:
        """把冲突/互补条目放进待仲裁队列（同文本不重复入队）"""
        items = self._load_pending()
        if any(i.get("text") == text for i in items):
            return {"queued": False, "reason": "duplicate"}
        items.append({"text": text, "metadata": metadata or {},
                      "status": status, "detail": detail, "ts": time.time()})
        self._save_pending(items)
        return {"queued": True, "size": len(items)}

    def list_pending(self) -> list:
        return self._load_pending()

    def resolve_pending(self, index: int, keep_new: bool,
                        coll: str = "knowledge_fact") -> dict:
        """处理第 index 条待仲裁：采纳则入库，丢弃则移除"""
        items = self._load_pending()
        if index < 0 or index >= len(items):
            return {"ok": False, "error": "索引越界"}
        item = items.pop(index)
        doc_id = None
        if keep_new:
            doc_id = self.add_knowledge(item.get("text", ""),
                                        metadata=item.get("metadata") or {}, coll=coll)
        self._save_pending(items)
        return {"ok": True, "action": "kept_new" if keep_new else "dropped",
                "doc_id": doc_id, "remaining": len(items)}


knowledge_service = KnowledgeService()
