"""
ReAct Agent主调度器
推理循环：思考 -> 检索记忆/技能 -> 选择工具 -> 执行工具 -> 观察结果 -> 写入沙盘/记忆 -> 循环直到任务完成
召回优先级：knowledge_skill > knowledge_fact > knowledge_rule > episode_memory > 当前沙盘索引
"""
import uuid
import os
import time
import re
import json
import inspect
from core.logger import root_logger, audit
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.memory_scorer import SCORE_SAVE
from core.model_router import router
from core.events import bus
from core.metrics import metrics
from core.context_compressor import context_compressor
from core.session_manager import session_manager
from core.cache.schema_cache import build_or_get_schema
from services.knowledge_service import knowledge_service
from services.memory_service import memory_service
from services.imaginary_service import imaginary_service
from core.sandbox_index import index_search
from tools.desktop_control_tool import GLOBAL_QUEUE, plugin_export as desktop_plugin
from tools.vl_vision_tool import plugin_export as vl_plugin
from tools.software_help_reader import plugin_export as learn_plugin
# 联网检索 + 网页抓取已改为功能插件（plugins/installed/net_plugin，2026-11-09）
from tools.ask_user_tool import plugin_export as ask_plugin
# 想象沙盘已改为功能插件（plugins/installed/space_plugin，2026-11-09）
# 文件夹扫描 / 读书已改为功能插件（plugins/installed/reading_plugin，2026-11-09）
# 提示词助手已改为功能插件（plugins/installed/prompt_plugin，2026-11-09）
# 辩论（debate_start / debate_speak / debate_extract / debate_list）已改为功能插件
# （plugins/installed/debate_plugin，2026-11-09），不再内置注册
# 工具链宏（run_toolchain / toolchain_list）已改为功能插件
# （plugins/installed/toolchain_plugin，2026-11-09），不再内置注册；它不带面板页
# 科研七步已改为功能插件（plugins/installed/research_plugin，2026-11-09），
# 不再作为内置插件注册：工具由 registry.discover 提供，面板页由 manifest.panel 声明。
# 运行模式（mode_status / mode_set）已改为功能插件
# （plugins/installed/mode_plugin，2026-11-09），面板页由 manifest.panel 声明
# 读书（book_read / book_list）已并入 plugins/installed/reading_plugin，不再内置注册
from tools.tutor_tool import plugin_export as tutor_plugin
from tools.obsidian_tool import plugin_export as obsidian_plugin
from tools.multi_model_tool import plugin_export as multi_model_plugin
from tools.workspace_tool import plugin_export as workspace_plugin
# 联网检索（web_search）与网页抓取（browser_fetch）已并入 plugins/installed/net_plugin，不再内置注册
from core.plugin import registry

# 全部工具注册表（plugin 注册表双轨 + 兼容层）
ALL_TOOLS = {}
_PLUGIN_SOURCES = [("desktop", desktop_plugin), ("vision", vl_plugin),
                   ("learn", learn_plugin),
                   ("ask", ask_plugin),
                   ("tutor", tutor_plugin), ("obsidian", obsidian_plugin),
                   ("multi_model", multi_model_plugin),
                   ("workspace", workspace_plugin)]
for _name, _p in _PLUGIN_SOURCES:
    ALL_TOOLS.update(_p)
    registry.register_builtin(_name, _p, desc=f"内置插件：{_name}")

# 启动时注册外部插件（plugins/installed + plugins/market），供主循环 _resolve_tool 懒加载
try:
    from core.plugin.loader import DEFAULT_ROOTS as _PLUGIN_ROOTS
    registry.discover(_PLUGIN_ROOTS)
except Exception as _e:
    root_logger.warning(f"外部插件注册失败：{_e}")


def _model_can_use_thought(model: str) -> bool:
    """小于 7B 的模型不注入方法论：元指令遵从度太低，注入只会让它更发散。

    从模型名里取参数量（qwen3.5:4b → 4、deepseekR1Q7B:latest → 7、llama3.1:8b → 8）；
    名字里没有 b 数的（如 deepseek-chat 这类云端模型）一律按支持处理。
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB]", model or "")
    return True if not m else float(m.group(1)) >= 7


def _build_tool_desc() -> str:
    """工具描述预编译缓存（schema_cache），避免每轮重复拼装；
    包含内置工具 + 外部插件（热加载）工具"""
    def _builder():
        merged = dict(ALL_TOOLS)
        for tname, spec in registry.all_tools().items():
            merged.setdefault(tname, spec)
        return "\n".join([f"- {k}: {v['desc']}" for k, v in merged.items()])
    return build_or_get_schema("tool_desc_v2", _builder)


TOOL_DESC = _build_tool_desc()


def _resolve_tool(tool_name: str):
    """工具解析：内置 ALL_TOOLS 优先，未命中回落外部插件注册表（懒加载）"""
    spec = ALL_TOOLS.get(tool_name)
    if spec is not None:
        return spec
    ext = registry.get_tool(tool_name)
    if ext is not None:
        return {"desc": ext.desc, "func": ext.func}
    return None

SYSTEM_PROMPT = """
你是一个三层记忆架构Agent。
记忆层级召回优先级：
1. knowledge_skill 软件技能（最高优先）
2. knowledge_fact 事实知识
3. knowledge_rule 系统规则
4. episode_memory 情景记忆（随时间衰减归档）
5. 想象沙盘（当前会话草稿、截图、MD记录，仅当前会话可用）

可用工具列表：
__TOOL_DESC__

约束：
1. 桌面操作工具提交后会进入审批队列，必须等待人工确认，不能假定自动执行
2. 路径无法确认时，禁止编造路径
3. 软件自学成功后，技能会自动存入知识库，下次相同任务优先召回技能，不再重复自学
4. 每一步思考、工具返回结果写入想象沙盘
5. 任务完成后，评估本次情景记忆分数，存入episode_memory
6. 循环格式：
Thought: 你的思考
Action: 工具名 | 参数json（仅当需要调用工具时输出这一行，不需要工具时禁止输出Action）
Observation: 工具返回结果
7. 重要规则：
- 纯推理、计算、分析类任务通常不需要任何工具，一轮即可完成，直接以 FinalAnswer: 开头给出完整最终答案
- 不要重复已经给出过的答案或推理
- 最终答案必须以 FinalAnswer: 开头
8. 图形表达原则：
- 当图形/符号/图表能比纯文字更简洁清晰地表达关系、公式证明、结构、流程时，应当使用 draw_mermaid 等绘图工具输出
- 例如公式的证明过程、系统的结构关系、流程顺序——"怎么来的"比"一个数值结果"更有价值；图形能清楚展示证明路径
- 数值计算重要，但只有结果没有过程时，无法理解来龙去脉；善用图形+符号把证明/关系的过程记录清楚
"""

def _clean_final_answer(text: str) -> str:
    """清洗最终答案：剥离包裹整个输出的单个markdown代码块标记"""
    t = text.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n```$", t, re.DOTALL)
    if m:
        return m.group(1).strip()
    return t


def _sink_conclusion(user_task: str, final: str) -> dict:
    """把任务结论沉淀进知识库（冲突检测+自动仲裁，仅存有信息量的结论）"""
    if not final or len(final.strip()) < 30:
        return None
    try:
        return knowledge_service.add_knowledge_safe(
            f"任务结论：{user_task} → {final[:200]}",
            metadata={"source": "react_final"}, coll="knowledge_fact", auto_resolve=True)
    except Exception as e:
        root_logger.warning(f"结论沉淀知识库失败：{e}")
        return None


def _finish_task(session_id: str, user_task: str, final: str, history_log: list) -> str:
    """任务正常结束收尾：记忆打分入库 + 结论沉淀知识库 + 写final日志"""
    score_prompt = f"给本次任务推理质量打分，0-10，只输出数字：{user_task} 结果：{final}"
    # 评分走轻模型 + 持久化缓存（确定性调用）
    score_resp = ask([{"role": "user", "content": score_prompt}],
                                 temperature=0.1,
                                 model=router.route("score"),
                                 use_cache=True)
    try:
        score = float(score_resp.content.strip())
    except (ValueError, TypeError):
        score = 6.0
    if score >= SCORE_SAVE:
        memory_service.add_episode_memory(session_id, f"任务：{user_task} 结果：{final}", base_score=score)
    _sink_conclusion(user_task, final)
    history_log.append(f"FinalAnswer: {final}")
    imaginary_service.write_md(session_id, "react_final.md", "\n".join(history_log))
    # 想象空间节点化：任务结论入节点（结构化，可被 list_nodes/validator 管理），失败静默
    try:
        imaginary_service.create_node(session_id, f"任务结论：{user_task[:40]}",
                                      final[:800], node_type="react")
    except Exception as e:
        root_logger.warning(f"沙盘结论节点创建失败: {str(e)[:80]}")
    # 逻辑校验：发现矛盾追加到 final 日志尾部（只读校验，失败静默）
    try:
        vr = imaginary_service.validate(session_id)
        if vr and vr.get("contradictions"):
            detail = str(vr["contradictions"])[:300]
            fp = os.path.join(imaginary_service.sandbox_root, session_id, "react_final.md")
            with open(fp, "a", encoding="utf-8") as f:
                f.write(f"\n\n## 逻辑校验发现矛盾\n{detail}")
            root_logger.info(f"沙盘校验发现矛盾并回写: {detail[:80]}")
    except Exception as e:
        root_logger.warning(f"沙盘逻辑校验失败: {str(e)[:80]}")
    return final


def _maybe_compact(messages: list, threshold: int = 14000, keep_recent: int = 4) -> bool:
    """上下文守护：messages 总长超阈值时，把中间历史轮次压缩成摘要（保留 system+最近轮次）
    threshold=14000 字符：实测中文 1.66 字符/token，14000 字符≈8423 token，占 16384 窗口 51%，
    剩约 8000 token 给生成。原 6000 字符只占 22%，压缩触发过早，会把中间轮的 Observation
    （模型唯一的外置状态）压掉，长任务因此"失忆"。"""
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total <= threshold:
        return False
    if len(messages) <= keep_recent + 1:
        return False
    head = messages[:1]
    tail = messages[-keep_recent:]
    mids = messages[1:-keep_recent]

    # 按 role 区分：assistant 是模型自语（猜测，可丢），user 是代码给的 Observation（事实，不能丢）
    notes, obs, dropped = [], [], 0
    for m in mids:
        c = str(m.get("content", ""))
        if m.get("role") == "assistant":
            notes.append(c[:200])
        else:
            obs.append(c)

    # Observation 保留最近的（预算 8000 字符，与 A 层单条上限 8000 对齐）
    # 超出预算的最近一条做截断而不是整条丢，否则 8000 字符的观测一条都留不下
    budget, kept, used = 8000, [], 0
    for c in reversed(obs):
        room = budget - used
        if room < 200:
            dropped += 1
            continue
        if len(c) > room:
            c = context_compressor.summarize_observation(c, max_len=room)
        kept.append(c)
        used += len(c)
    kept.reverse()

    parts = ["【历史轮次摘要】"]
    if notes:
        parts.append("推理部分：\n" + "\n".join(notes)[:600])
    if dropped:
        parts.append(f"更早的 {dropped} 条观测已省略")
    parts.extend(kept)
    messages[:] = head + [{"role": "user", "content": "\n\n".join(parts)}] + tail
    # 兜底：压缩后仍超阈值，做确定性截断（assistant 自语 1200 / Observation 4000），不改写内容
    left = sum(len(str(m.get("content", ""))) for m in messages)
    if left > threshold:
        for m in messages[1:]:
            limit = 1200 if m.get("role") == "assistant" else 4000
            c = str(m.get("content", ""))
            if len(c) > limit:
                m["content"] = context_compressor.summarize_observation(c, max_len=limit)
    root_logger.info(
        f"上下文压缩：{total}字符 → 推理摘要{len(notes)}条 / 观测留存{len(kept)}条 / 省略{dropped}条")
    return True


def _store_long_obs(session_id: str, obs, round_no: int, limit: int = 8000) -> str:
    """超长观测：全文落沙盘，返回给模型的只是预览 + 路径，需要细节时用 sandbox_read 回查。
    落盘失败则回退确定性截断，保证不因沙盘不可写而撑爆上下文。"""
    if not isinstance(obs, str):
        obs = str(obs)
    if len(obs) <= limit:
        return obs
    try:
        fp = imaginary_service.write_md(session_id, f"obs_r{round_no}_{int(time.time())}.md", obs)
        return f"{obs[:2000]}\n…[全文 {len(obs)} 字符已存沙盘：{fp}；需要细节用 sandbox_read 读取]"
    except Exception as e:
        root_logger.warning(f"观测落盘失败，回退截断：{e}")
        return context_compressor.summarize_observation(obs, max_len=4000)


def react_loop(session_id: str, user_task: str, max_round=8, model: str = None):
    """
    ReAct循环主函数
    :param session_id: 会话id
    :param user_task: 用户原始任务
    :param max_round: 最大推理轮次，防止死循环
    :param model: 指定执行模型；None 用路由 main（画图/工具密集任务可传 light 模型提高 Action 格式遵从度）
    """
    audit(f"REACT_START | sid={session_id} | task={user_task}")
    session_manager.register(session_id)
    from core.mode import mode_strategy, check as mode_check, pre_check as risk_check, mode_prompt_segment
    from core.model_router import router
    exec_model = model or router.route("main")
    mode = mode_strategy.predict_mode(user_task)
    system = SYSTEM_PROMPT.replace("__TOOL_DESC__", _build_tool_desc()) \
        + "\n\n## 模式约束\n" + mode_prompt_segment(mode)
    # 思维架构：ReAct 是主干，方法论是可选辅助。
    # 注入"判别信号 + 它在解决什么 + 示范步骤"，不是"必须走完的表格"：
    # 只给编号步骤会让模型套模板——既不判断方法可行性，也不会变通（实测两道冷题全错）。
    # 同时给出换方法的出口，判断不适用就别硬套。
    # C：<7B 整段跳过。实测 4B 读到方法名 4 次却没写一次"采用"，反而在同一前提上打转 4 遍。
    # 注：MODES 只有 steady/inquiry 两种，原先的 mode != "chat" 是恒真条件，已改正。
    if mode in ("steady", "inquiry") and _model_can_use_thought(exec_model):
        try:
            from core.thought_lib import thought_lib
            hits = thought_lib.recommend(user_task, max_items=1)
            seg = "\n\n## 思维架构（可选辅助）\n"
            if hits:
                arch = thought_lib.get(hits[0])
                seg += (
                    f"### 首选：{arch.title}\n"
                    f"这类题的判别信号：{arch.desc}\n"
                    f"它在解决什么：{arch.core()}\n"
                    f"示范步骤（按题目需要取用、合并或跳过，不必逐条抄成六行）：\n{arch.steps()}\n"
                )
            else:
                seg += "这道题没对上某个具体方法。\n"
            seg += (
                "先判断，再动手：\n"
                "- 出现了上面的信号 → 借它的核心思路自己组织推理，顺序由你按题目决定。\n"
                "- 没出现，或你判断它解不了这道题 → 别硬套，从下面换一个，或者干脆不用。\n"
                "### 其他可选（判断不适用时从这里换）\n" + thought_lib.catalog() + "\n"
                "用错方法比不用更糟。Action/Observation 的格式不变。"
            )
            system += seg
        except Exception as e:
            root_logger.warning(f"思维架构注入失败: {str(e)[:100]}")
    messages = [{"role": "system", "content": system}]
    messages.append({"role": "user", "content": user_task})
    history_log = [f"# ReAct推理日志 | sid={session_id}\n任务：{user_task}"]
    last_output = ""  # 记录上一轮输出，用于重复检测
    short_retry = 0   # 短输出追问次数：模型答得像思考片段时只追问一次，避免死循环

    for r in range(max_round):
        root_logger.info(f"===== ReAct 第{r+1}轮 =====")
        session_manager.heartbeat(session_id)
        # 只在第一轮注入召回记忆：后续轮次复用对话上下文，避免相同注入导致模型重复回答
        if r == 0:
            skill_hits = knowledge_service.search_skill(user_task, top_k=2)
            # 事实知识召回同样走 ranked_search：按来源权重重排，求证结论优先
            fact_hits = knowledge_service.ranked_search(user_task, top_k=2)
            sandbox_hits = index_search(session_id, user_task)
            recall_text = "【召回参考】\n## 技能\n"
            for s in skill_hits:
                recall_text += s["document"] + "\n---\n"
            recall_text += "## 事实知识\n"
            for f in fact_hits:
                recall_text += f["text"] + "\n---\n"
            recall_text += "## 沙盘草稿索引\n"
            for sb in sandbox_hits[:3]:
                recall_text += str(sb) + "\n"
            messages.append({"role": "user", "content": recall_text + "\n基于上面参考，继续推理，选择工具或给出最终答案"})

        resp = ask(messages, temperature=0.3, profile="deep", model=exec_model, tag="react")
        if not resp.success:
            history_log.append(f"轮次{r+1} LLM失败：{resp.content}")
            break
        content = resp.content.strip()
        if not content:
            history_log.append(f"轮次{r+1} 模型输出为空")
            break

        # 重复检测：与上一轮输出相同，说明模型在空转，立即终止
        if content == last_output:
            history_log.append(f"## 轮次{r+1} 输出与上一轮完全重复，提前终止")
            root_logger.warning(f"第{r+1}轮检测到输出重复，提前终止循环")
            break
        last_output = content
        history_log.append(f"## 轮次{r+1} 输出：\n{content}")
        imaginary_service.write_md(session_id, f"react_round_{r+1}.md", content)
        bus.publish("session", {"sid": session_id, "round": r + 1, "phase": "model_output"})

        # ---- 结束判定1：显式答案标记 ----
        final_marker = None
        for marker in ("FinalAnswer:", "最终答案：", "最终答案:"):
            if marker in content:
                final_marker = marker
                break
        if final_marker:
            final = _clean_final_answer(content.split(final_marker)[-1].strip())
            history_log.append(f"✅ 任务结束 FinalAnswer：{final}")
            return _finish_task(session_id, user_task, final, history_log)

        # ---- 结束判定2：无工具调用意图的完整回答，直接作为最终答案 ----
        # 不含 Action: 说明模型认为不需要调用工具，纯推理/计算/分析任务到这里即为完成
        if "Action:" not in content:
            # 过滤掉可能只是思考片段的极短输出，避免把半截Thought当答案。
            # 但只追问一次：llama 这类模型会把"10"这种正确短答案直接给出，
            # 旧逻辑每次判定<20字就再追问，一路追问到 max_round 空转。
            if len(content) >= 20 or short_retry >= 1:
                history_log.append("✅ 任务结束（无工具调用，直接采纳模型回答）")
                return _finish_task(session_id, user_task, _clean_final_answer(content), history_log)
            # 太短，疑似思考片段：追问完整答案
            short_retry += 1
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "请直接给出最终答案，不要只输出思考片段，以 FinalAnswer: 开头。"})
            continue

        # ---- 有 Action: 解析并执行工具 ----
        try:
            action_part = content.split("Action:")[-1].split("Observation:")[0].strip()
            tool_name, arg_json = action_part.split("|", 1)
            args = json.loads(arg_json.strip())
            tool_func = _resolve_tool(tool_name.strip())
            if not tool_func:
                obs = f"工具不存在：{tool_name}"
            else:
                # ---- 模式权限 + 风险前置检查 ----
                allow, reason = mode_check(mode, tool_name.strip())
                if not allow:
                    obs = f"【模式拦截】{reason}"
                else:
                    allow_r, reason_r = risk_check(mode, tool_name.strip(), args)
                    if not allow_r:
                        obs = f"【风险拦截】{reason_r}"
                    else:
                        # 仅当工具签名接受 session_id 时才注入，避免签名不含该参数的工具 TypeError
                        sig = inspect.signature(tool_func["func"])
                        if "session_id" in sig.parameters:
                            args.setdefault("session_id", session_id)
                        with metrics.timer(f"tool_{tool_name.strip()}"):
                            obs = tool_func["func"](**args)
                        bus.publish("tool", {"sid": session_id, "tool": tool_name.strip(), "round": r + 1})
        except Exception as e:
            # 只回裸错误，模型不知道该怎么改，会原样重试直到轮次耗尽（实测 llama 空转 6 轮）
            obs = (f"Action解析失败：{str(e)}\n"
                   f"格式必须是单行：Action: 工具名 | {{\"参数\": \"值\"}}，竖线后必须是合法 JSON。"
                   f"如果已经能得出结论，别再调工具，直接以 FinalAnswer: 开头给出答案。")
        # observation 超长：>8000 字符全文落沙盘，上下文只留预览 + 路径（可用 sandbox_read 回查）
        obs = _store_long_obs(session_id, obs, r + 1)
        history_log.append(f"Observation: {obs}")
        # 模型输出回写上下文，保证下一轮模型能看到自己说了什么
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": f"Observation: {obs}"})
        # 上下文守护：超阈值压缩历史轮次，防止长任务把上下文撑爆
        _maybe_compact(messages)
        time.sleep(0.5)

    # ---- 超时兜底：返回最后一次有效输出，而不是丢弃结果 ----
    imaginary_service.write_md(session_id, "react_timeout.md", "\n".join(history_log))
    # 但"最后输出"若仍是工具动作（含 Action:），那不是答案：旧版原样返回，用户会看到
    # 「Action: calc | {...}」这种半截文本当结论。撞轮次上限和检测到重复都会走到这里。
    if last_output and "Action:" not in last_output:
        root_logger.warning("达到最大推理轮次，返回最后一次有效输出")
        return last_output
    if last_output:
        last_obs = str(messages[-1].get("content", ""))[:200] if messages else ""
        root_logger.warning(f"达到最大推理轮次，最后输出仍是工具动作（轮次 {max_round}）")
        return f"[未完成] 达到轮次上限 {max_round}，最后一次输出仍是工具动作，没有给出结论。最后观测：{last_obs}"
    return "达到最大推理轮次，任务终止"

def start_background_services() -> None:
    """启动全部后台服务（幂等，main __main__ 与 dashboard 共用）：
    记忆归档 / 会话清理 / LLM 缓存清理 / 闲聊归档 / 插件热加载"""
    from core.async_pool import async_pool
    from core.plugin.loader import hot_loader
    from services.memory_service import memory_service
    from core.session_manager import session_manager
    from core.cache.llm_cache import llm_cache
    # 记忆衰减归档后台任务：每小时扫描全量情景记忆，低于阈值标记归档
    async_pool.schedule_periodic(memory_service.scan_and_archive, 3600.0,
                                 "memory_archive", run_immediately=False)
    # 会话过期自动清理：每5分钟检查一次闲置超时会话（默认 1800s），清理沙盘目录
    async_pool.schedule_periodic(session_manager.cleanup_idle, 300.0,
                                 "session_cleanup", run_immediately=False)
    # LLM 磁盘缓存清理：每天清一次过期（>TTL）缓存文件，防 cache/llm_cache 无界增长
    async_pool.schedule_periodic(llm_cache.cleanup, 86400.0,
                                 "llm_cache_cleanup", run_immediately=False)
    # 闲聊记录归档：每天把 chat.md 前缀移到 archive/，保留最近 200 行
    from services.chat_archive import archive as chat_archive
    async_pool.schedule_periodic(chat_archive, 86400.0, "chat_archive",
                                 run_immediately=False)
    # 记忆分区集合预热：只有面板进程负责建集合（避免读进程 get_or_create 竞争）
    async_pool.submit(memory_service.ensure_collections)
    # 旧 episode_memory → 分区记忆的一次性迁移（幂等，后台执行，不阻塞启动）
    async_pool.submit(memory_service.migrate_legacy)
    hot_loader.start()


if __name__ == "__main__":
    print("===== ReAct Agent 启动 =====")
    start_background_services()
    sid = str(uuid.uuid4())
    print(f"当前会话sid={sid}")
    while True:
        task = input("\n请输入任务（exit退出）：")
        if task.strip().lower() == "exit":
            break
        res = react_loop(sid, task, max_round=8)
        print("\nAgent返回结果：")
        print(res)
