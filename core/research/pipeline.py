# core/research/pipeline.py
"""
科学猜想推理七步流水线（用户原创方法论落地）：
Step1 定性找对象 → Step2 定性分析（相关性大→小）→ Step3 因果假设
→ Step4 现实验证（多渠道）→ Step5 公共量收敛→单位量
→ Step6 定量推导（关系式+系数，可计算）→ Step7 真理性验证（现象解释/逻辑自洽）

- 研究目录：sandbox/research/<topic_id>/ 下 stepN_<name>.md + status.json
- 每步调用 LLM 生成结构化 JSON（json_extract 鲁棒解析），支持中断续跑
"""
import os
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from core.logger import root_logger
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.model_router import router
from core.json_extract import extract_json

# 本文件在 core/research/ 下，上三级才是项目根。
# 原先只上两级 → 落到 core/sandbox/research，测试跑一次堆一个空壳目录（实测 52 个）。
RESEARCH_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "sandbox", "research")

STEPS = {
    1: ("define_object", "确定研究对象：给出明确的研究对象与目标问题"),
    2: ("qualitative_scan", "定性分析：列出与研究对象相关联的量，按相关性大→小排序"),
    3: ("causal_assume", "因果假设：从最大相关量入手，假设其与对象存在因果关系"),
    4: ("reality_check", "现实验证：通过知识库/资料/观察/辩论验证因果是否真实存在，汇总有因果关系的量"),
    5: ("converge_units", "公共量收敛：分析各量间共同关系，收敛出单位量（可构建其他量的基本量）"),
    6: ("quantitative_derive", "定量推导：用单位量经逻辑推理得到关系表达式与系数，可用数学计算（量纲齐次）"),
    7: ("truth_verify", "真理性验证：用结论解释生活现象（物理）或验证逻辑自洽（数学）；给出成立/存疑/证伪"),
}

HEAVY_STEPS = {3, 6, 7}   # 语义上最重的三步；2026-11-09 起七步统一走 light（原因见 research_step）

# 需要外部资料的步骤（2026-11-08 补：此前七步全是 ask(prompt) 自转，零外部信息 → 闭环）
# 2 定性分析要真知道「和什么相关」；4 现实验证要真证据。其余步骤靠前面几步的推导。
RETRIEVAL_STEPS = {2, 4}


def _query_variants(topic: str, step: int):
    """
    把长课题拆成 bing 吃得下的短查询，并返回用于相关性过滤的核心词。

    实测（2026-11-08，同一课题五种写法）：
      「沸点 海拔 对照表」        → 命中沸点原理/气压影响，可用
      「水的沸点与海拔高度的关系」 → 全退化成「水是什么」的字典百科，不可用
      「water boiling point ...」 → 全退化成 water 词典，不可用
    规律：整句会被 bing 拆坏，且「X的」里的高频单字（水/人/力）会被抬成主查询词。
    所以先剥抽象词尾巴 → 删「X的」→ 再按连词切成 2-3 个具体名词。
    :return (queries, keys)
    """
    t = (topic or "").strip()
    for w in ("之间的关系", "之间关系", "的关系", "关系", "影响因素", "影响机理", "影响",
              "变量", "研究", "分析", "探讨", "问题", "现象"):
        t = t.replace(w, " ")
    # 按「的/与/和/及/标点」统一切分，**每段保留完整名词短语**。
    # 曾用 re.sub(r"[\u4e00-\u9fa5]{1,3}的", " ") 删「X的」，结果「运算放大器的带宽」
    # 里的「放大器的」被当成修饰语吃掉，查询变成「运算 带宽」→ bing 返回位运算教程。
    # 正确做法是切分而不是删除：切完只丢 1 字碎片（水/人/力这类会主导 bing 的高频单字）。
    keys = []
    for p in re.split(r"的|与|和|及|、|，|,|。|；|;|：|:|—|-|\s+", t):
        k = p.strip()
        if len(k) >= 2 and k not in keys:
            keys.append(k)
    if not keys:
        keys = [(topic or "").strip()[:20]]
    whole = " ".join(keys[:3])
    tail = "关系" if step == 2 else "数据"
    out = []
    for q in (f"{whole} {tail}", f"{whole} 对照表", f"{whole} 原理"):
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out[:2], keys


def _fetch_external(topic: str, step: int) -> dict:
    """
    为第 2/4 步抓外部资料：网页检索（core/web_search）+ 本地知识库。
    任何一环失败都只记 note，不抛异常——网络断了就退化成纯 LLM 推理，流程不能断。
    :return {"queries": [...], "web": [{title,domain,snippet,url}], "kb": [{doc,dist}], "ok": bool, "notes": [...]}
    """
    ext = {"queries": [], "web": [], "kb": [], "std": [], "lib": [], "ok": False, "notes": []}
    queries, keys = _query_variants(topic, step)
    try:
        from core import web_search as _ws
    except Exception as e:
        ext["notes"].append(f"检索模块不可用：{str(e)[:60]}")
        _ws = None
    if _ws is not None:
        seen_url = set()
        kept = []
        for q in queries:
            try:
                items = _ws.search(q, limit=6) or []
            except Exception as e:
                ext["notes"].append(f"检索「{q[:20]}」异常：{str(e)[:60]}")
                items = []
            ext["queries"].append(q)
            for it in items:
                url = it.get("url", "")
                if url in seen_url:
                    continue
                seen_url.add(url)
                # 相关性过滤：标题+摘要里必须出现核心词，否则就是 bing 把查询退化成别的词了
                blob = (it.get("title") or "") + " " + (it.get("snippet") or "")
                if keys and not any(k in blob for k in keys):
                    continue
                try:
                    dom = _ws.domain_of(url)
                except Exception:
                    dom = ""
                kept.append({"title": (it.get("title") or "")[:80], "domain": dom,
                             "snippet": (it.get("snippet") or "")[:160], "url": url})
            if len(kept) >= 4:      # 够用就停，小模型读不完
                break
        ext["web"] = kept[:5]
        if keys and not ext["web"]:
            ext["notes"].append(f"检索命中但均与核心词（{'/'.join(keys[:3])}）无关，已丢弃")
        # 【2026-11-09 实测结论，勿重复踩】曾试过对命中再抓正文补定量数据，无效：
        # bing 裸抓（无 cookie）对复杂查询会归一到最热的核心词——「沸点 0.33 100米 海拔」
        # 仍返回《沸点_百度百科》，「水的沸点 每升高100米 下降0.33」退化成「水」字典页，
        # site: 也不生效；前 3 条不是反爬的百科就是站点导航噪音。
        # 所以免费抓页只能当「发现相关量」的线索源，定量数据必须靠有 API 的搜索。
    try:
        from services.knowledge_service import knowledge_service
        hits = knowledge_service.search_knowledge(topic, top_k=4) or []
        for h in hits[:3]:
            meta = h.get("metadata") or {}
            src = str(meta.get("source", ""))
            if "hypothesis" in src.lower():
                # 已删猜想库的沉淀（全是「这里是基于知识的任务结论」空模板），不是真知识
                continue
            doc = (h.get("document") or "").strip()
            if len(doc) < 12:
                continue
            ext["kb"].append({"doc": doc[:200], "dist": h.get("distance")})
    except Exception as e:
        ext["notes"].append(f"知识库检索失败：{str(e)[:60]}")
    # 国家标准题录（openstd 公开接口，无 key 无验证码）。
    # 只有题录：标准号/名称/状态，正文是逐页图片拿不到数值——
    # 但对工程类课题很有用（「这个量有哪些国标在测」），对纯数学研究则常常空手。
    try:
        from core.gb_std import search_standards
        kw = (keys[0] if keys else topic)[:18]
        ext["std"] = search_standards(kw, limit=4) or []
    except Exception as e:
        ext["notes"].append(f"国标题录检索失败：{str(e)[:60]}")
    # 外部资料库（本地教材/数据手册，路径在面板「地址管理器」配置）。
    # 只按文件名召回，命中才抽一本书的正文。
    # 2026-11-09 起 read() 带按需 OCR：扫描版抽不到文字层时，会渲染 1~3 页识别，
    # 单本约 12~18s。所以 OCR 只给前 2 本开——书库常有同书多份，第一本成功就 break，
    # 真到第 3 本说明前两本都没救，再 OCR 也只是白等。
    try:
        from core import external_kb as _ekb
        hits = _ekb.search(topic, limit=4) or []
        tried = []
        for idx, h in enumerate(hits[:4]):
            if h["rel"] in tried:          # 多试几本：头几本常是扫描版或残缺本
                continue
            tried.append(h["rel"])
            body = _ekb.read(h["path"], pages=2, maxlen=240, ocr=(idx < 2))
            if body and len(body.strip()) >= 60:
                ext["lib"].append({"name": h["name"], "rel": h["rel"], "body": body})
                break
            msg = f"《{h['name'][:24]}》未取到可用正文，未采用"
            if msg not in ext["notes"]:
                ext["notes"].append(msg)
    except Exception as e:
        ext["notes"].append(f"外部资料库检索失败：{str(e)[:60]}")
    ext["ok"] = bool(ext["web"] or ext["kb"] or ext.get("std") or ext.get("lib"))
    return ext


def _render_external(ext: dict, limit: int = 3) -> str:
    """
    把外部资料拼成 prompt 里的一段。

    长度是硬约束：端到端实测，第 2 步带上 5 条完整摘要后 prompt 变长，
    本地小模型单次调用跑到 337s（并触发两次 120s 超时重试）。
    所以这里只给 3 条、摘要压到 100 字，整个块控制在 600 字符内——
    外部资料是拿来"定方向"的，不是拿来"读完"的。
    """
    if not ext:
        return "（未启用外部检索）"
    if not ext.get("ok"):
        note = "；".join(ext.get("notes") or []) or "检索无命中"
        return f"（本步未取到外部资料：{note}）"
    parts = []
    if ext.get("web"):
        parts.append("【网页检索结果】")
        for i, it in enumerate(ext["web"][:limit], 1):
            parts.append(f"[{i}] {it['title']}（{it['domain']}）")
            if it.get("snippet"):
                parts.append(f"    {it['snippet'][:100]}")
    if ext.get("kb"):
        parts.append("【本地知识库】")
        for i, k in enumerate(ext["kb"][:2], 1):
            parts.append(f"[{i}] {k['doc'][:100]}")
    if ext.get("std"):
        parts.append("【相关国家标准（只有题录，无正文数值）】")
        for i, s in enumerate(ext["std"][:3], 1):
            tag = s.get("status") or ""
            parts.append(f"[{i}] {s['code']} {s['name'][:40]}" + (f"（{tag}）" if tag else ""))
    if ext.get("lib"):
        parts.append("【本地资料库原文摘录】")
        for i, k in enumerate(ext["lib"][:1], 1):
            parts.append(f"[{i}] 《{k['name'][:34]}》\n{k['body'][:200]}")
    text = "\n".join(parts)
    return text[:600]


@dataclass
class ResearchStatus:
    topic_id: str
    topic: str
    step: int = 1                     # 当前待执行步（1-7）
    status: str = "running"           # running / completed / stuck
    steps: dict = field(default_factory=dict)   # step -> 结构化结果
    findings: list = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)


def _research_dir(topic_id: str) -> str:
    d = os.path.join(RESEARCH_ROOT, topic_id)
    os.makedirs(d, exist_ok=True)
    return d


def _status_path(topic_id: str) -> str:
    return os.path.join(_research_dir(topic_id), "status.json")


def _load_status(topic_id: str) -> Optional[ResearchStatus]:
    p = _status_path(topic_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        # JSON 的键一律是字符串，读回来必须转回 int，
        # 否则内存里会同时存在 steps[1] 和 steps["1"] 两份，且按 int 查永远查不到历史
        raw = data.get("steps") or {}
        fixed = {}
        for k, v in raw.items():
            try:
                fixed[int(k)] = v
            except (TypeError, ValueError):
                fixed[k] = v
        data["steps"] = fixed
        return ResearchStatus(**data)
    except Exception as e:
        root_logger.warning(f"研究状态读取失败: {e}")
        return None


def _save_status(st: ResearchStatus) -> None:
    st.updated_ts = time.time()
    with open(_status_path(st.topic_id), "w", encoding="utf-8") as f:
        json.dump(st.__dict__, f, ensure_ascii=False, indent=2)


def create_research(topic: str) -> dict:
    """新建研究课题，返回 topic_id 与第一步指引"""
    topic_id = uuid.uuid4().hex[:8]
    st = ResearchStatus(topic_id=topic_id, topic=topic)
    st.steps[1] = {"input": topic, "result": {"object": topic}}
    _save_status(st)
    return {"topic_id": topic_id, "step": 1, "step_name": STEPS[1][0],
            "prompt": STEPS[1][1], "status": "running"}


def get_research(topic_id: str) -> dict:
    st = _load_status(topic_id)
    if not st:
        return {"ok": False, "error": "研究课题不存在"}
    return {"ok": True, "topic_id": st.topic_id, "topic": st.topic,
            "step": st.step, "status": st.status,
            "steps": {k: v.get("result") if isinstance(v, dict) else v for k, v in st.steps.items()},
            "findings": st.findings}


def _step_of(st: ResearchStatus, k: int):
    """
    取第 k 步记录，兼容 int / str 两种键。

    坑：st.steps 在内存里用 int 键（create_research 写 steps[1]），
    json.dump → json.load 之后键全变成字符串 "1"、"2"。
    原实现写的是 `if k in st.steps`（int 查找），读回来的课题永远查不到历史，
    于是第 2 步以后每一步都在**失忆**状态下执行——这比"没有外部信息"更致命。
    """
    v = st.steps.get(k)
    if v is None:
        v = st.steps.get(str(k))
    return v


def _step_prompt(st: ResearchStatus, step: int, input_text: str, external: dict = None) -> str:
    name, hint = STEPS[step]
    ctx = ""
    for k in range(1, step):
        v = _step_of(st, k)
        if isinstance(v, dict):
            r = v.get("result", {})
            ctx += f"\n[第{k}步 {STEPS[k][0]}] {json.dumps(r, ensure_ascii=False)[:400]}"
    ext_block = ""
    if external is not None:
        ext_block = ("\n外部资料（下面是从网页和本地知识库真实检索到的，只能依据这些讲事实；"
                     "没有的事实就说不知道，不要编造来源或数据）：\n"
                     + _render_external(external) + "\n")
    return f"""科研流水线第{step}步（{name}）：{hint}
研究课题：{st.topic}
历史结果：{ctx or "（无）"}
{ext_block}
本步输入：{input_text}

请输出结构化 JSON（字段按本步需要自定义，含结论），只输出 JSON："""


def research_step(topic_id: str, step: int, input_text: str, retrieve: bool = True) -> dict:
    """执行指定步骤：外部检索（第2/4步）→ LLM 生成结构化结果 → 写研究目录 → 推进状态机"""
    st = _load_status(topic_id)
    if not st:
        return {"ok": False, "error": "研究课题不存在"}
    if st.status != "running":
        return {"ok": False, "error": f"研究已结束（{st.status}）"}
    if step != st.step:
        return {"ok": False, "error": f"当前应执行第{st.step}步（{STEPS[st.step][0]}），收到第{step}步"}
    name, _ = STEPS[step]

    # 外部信息注入：只对需要资料的步骤做，检索失败不阻断
    external = None
    if retrieve and step in RETRIEVAL_STEPS:
        try:
            external = _fetch_external(st.topic, step)
        except Exception as e:
            root_logger.warning(f"第{step}步外部检索异常（降级为纯推理）: {str(e)[:80]}")
            external = {"queries": [], "web": [], "kb": [], "ok": False,
                        "notes": [f"检索异常：{str(e)[:60]}"]}

    try:
        # 2026-11-09 真机实测（ollama 0.40.13，RTX3070 8GB）后定的模型与窗口策略：
        # ① main(deepseekR1Q7B) 的 think=False 在本机不生效：实测 content=0 字符、
        #    349 字全落进 thinking 且被 num_predict 截断 → extract_json 必然失败，
        #    而它推理只有 ~2.7 tok/s，一步就可能吃掉 70~180s。
        # ② 8GB 显存装不下三个模型（实测已用 7961/8192 MiB），light/main 交替会让
        #    8b 每次换入 20~25s；七步按 HEAVY_STEPS 要切 5 次，纯赔。
        # ③ 8b 在 num_ctx=12288 时 size_vram 只有 3.67/4.59GB（一半在 CPU，换入 85s），
        #    8192 时可完整驻留（实测 load 0~22s，单步 73~75s 稳定出 JSON）。
        # 故七步统一走 light，显式给 240s 超时（含换入余量）+ 8192 窗口。
        model = router.route("light")
        resp = ask([{"role": "user", "content": _step_prompt(st, step, input_text, external)}],
                   temperature=0.2, model=model, timeout=240, num_ctx=8192, tag="research")
        result = extract_json(resp.content) if resp.success else None
        if not isinstance(result, dict):
            result = {"raw": resp.content, "parsed": False}
    except Exception as e:
        return {"ok": False, "error": f"第{step}步 LLM 失败: {e}"}

    # 把检索痕迹留在结果里，便于事后核对「这一步到底有没有看到外部资料」
    if external is not None:
        result["_external"] = {"ok": external.get("ok"), "queries": external.get("queries", []),
                               "web_hits": len(external.get("web") or []),
                               "kb_hits": len(external.get("kb") or []),
                               "notes": external.get("notes", [])}

    st.steps[step] = {"input": input_text, "result": result, "ts": time.time()}
    st.steps.pop(str(step), None)      # 清掉旧实现可能留下的字符串键副本
    # 写研究目录 stepN_<name>.md
    try:
        with open(os.path.join(_research_dir(topic_id), f"step{step}_{name}.md"),
                  "w", encoding="utf-8") as f:
            f.write(f"# 第{step}步 {name}\n\n## 输入\n{input_text}\n")
            if external is not None:
                f.write(f"\n## 外部资料\n```\n{_render_external(external)}\n```\n")
            f.write(f"\n## 结构化结果\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```\n")
    except Exception as e:
        root_logger.warning(f"研究目录写入失败: {e}")

    if step >= 7:
        st.status = "completed"
        if isinstance(result, dict) and result.get("findings"):
            st.findings.extend(result["findings"])
    else:
        st.step = step + 1
    _save_status(st)
    return {"ok": True, "topic_id": topic_id, "step": step, "step_name": name,
            "result": result, "next_step": None if step >= 7 else st.step,
            "status": st.status}


def list_research(status: str = "") -> list:
    if not os.path.isdir(RESEARCH_ROOT):
        return []
    out = []
    for tid in os.listdir(RESEARCH_ROOT):
        st = _load_status(tid)
        if st and (not status or st.status == status):
            out.append({"topic_id": tid, "topic": st.topic, "step": st.step,
                        "status": st.status, "updated_ts": st.updated_ts})
    out.sort(key=lambda x: x["updated_ts"], reverse=True)
    return out
