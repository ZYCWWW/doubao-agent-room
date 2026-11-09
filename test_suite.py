# test_suite.py
"""
P1 地基单元测试（不依赖真实 LLM/Ollama）
运行：python test_suite.py  或  python -m unittest test_suite -v
"""
import os
import sys
import time
import json
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.json_extract import extract_json, extract_json_list, strip_code_fences
from core.events import EventBus
from core.metrics import Metrics, timer
from core.file_lock import file_lock, locked_write_text, locked_read_text, FileLockTimeout
from core.cache.memory_cache import TTLCache, NamespacedCache
from core.cache.llm_cache import LLMCache
from core.model_router import ModelRouter
from core.context_compressor import ContextCompressor
from core.session_manager import SessionManager, SANDBOX_ROOT
from core.async_pool import AsyncPool
from core.config_loader import config_loader, resolve_path


# ============ json_extract ============
class TestJsonExtract(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        text = '```json\n{"a": 1, "b": [1,2,3]}\n```'
        self.assertEqual(extract_json(text), {"a": 1, "b": [1, 2, 3]})

    def test_fence_no_lang(self):
        text = '```\n{"x": "y"}\n```'
        self.assertEqual(extract_json(text), {"x": "y"})

    def test_with_comments(self):
        text = """
        {
          // 注释
          "a": 1, /* 块注释 */
          "b": 2
        }
        """
        self.assertEqual(extract_json(text), {"a": 1, "b": 2})

    def test_string_with_slash(self):
        text = '{"url": "http://a.com//b", "ok": true}'
        self.assertEqual(extract_json(text), {"url": "http://a.com//b", "ok": True})

    def test_truncated_fix(self):
        text = '{"a": 1, "b": [1, 2'
        obj = extract_json(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj.get("a"), 1)

    def test_nested_in_text(self):
        text = '思考：需要工具。Action: scan_folder | {"path": "E:/x"}'
        self.assertEqual(extract_json(text), {"path": "E:/x"})

    def test_none_on_garbage(self):
        self.assertIsNone(extract_json("完全不是json"))

    def test_extract_list(self):
        self.assertEqual(extract_json_list('[{"a":1},{"b":2}]'), [{"a": 1}, {"b": 2}])

    def test_strip_fence(self):
        self.assertEqual(strip_code_fences("```python\nprint(1)\n```"), "print(1)")


# ============ events ============
class TestEvents(unittest.TestCase):
    def test_publish_subscribe(self):
        bus = EventBus()
        got = []
        bus.subscribe("memory", lambda t, p: got.append(p))
        bus.publish("memory", {"op": "add"})
        self.assertEqual(got, [{"op": "add"}])

    def test_snapshot_since(self):
        bus = EventBus(max_snapshot=10)
        bus.publish("tool", {"t": "a"})
        time.sleep(0.01)
        t0 = time.time()
        time.sleep(0.01)
        bus.publish("tool", {"t": "b"})
        snaps = bus.snapshot("tool", since_ts=t0)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0][2], {"t": "b"})

    def test_handler_exception_isolated(self):
        bus = EventBus()
        def bad(t, p):
            raise RuntimeError("boom")
        bus.subscribe("x", bad)
        bus.publish("x", 1)  # 不应抛出
        self.assertEqual(bus.topic_count("x"), 1)

    def test_latest(self):
        bus = EventBus()
        bus.publish("approval", {"aid": "1"})
        bus.publish("approval", {"aid": "2"})
        self.assertEqual(bus.latest("approval"), {"aid": "2"})


# ============ metrics ============
class TestMetrics(unittest.TestCase):
    def test_timer_records(self):
        m = Metrics()
        with timer("llm_chat", m):
            time.sleep(0.01)
        self.assertIsNotNone(m.avg("llm_chat"))
        self.assertGreaterEqual(m.avg("llm_chat"), 5.0)

    def test_counter(self):
        m = Metrics()
        m.count("hit")
        m.count("hit", 2)
        self.assertEqual(m.count_of("hit"), 3)

    def test_summary_shape(self):
        m = Metrics()
        m.record("a", 10)
        m.record("a", 20)
        m.record("a", 30)
        s = m.summary()
        self.assertIn("a", s["stages"])
        self.assertEqual(s["stages"]["a"]["count"], 3)
        self.assertAlmostEqual(s["stages"]["a"]["avg_ms"], 20.0)

    def test_p99(self):
        m = Metrics()
        for v in range(1, 101):
            m.record("b", v)
        self.assertGreaterEqual(m.p99("b"), 95.0)


# ============ file_lock ============
class TestFileLock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent_test_"))
        self.target = self.tmp / "data.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_read(self):
        locked_write_text(str(self.target), "hello")
        self.assertEqual(locked_read_text(str(self.target)), "hello")

    def test_lock_reentrant(self):
        with file_lock(str(self.target)):
            with file_lock(str(self.target)):  # 进程内可重入
                pass
        self.assertTrue(True)

    def test_lock_file_cleaned(self):
        with file_lock(str(self.target)):
            lock_file = self.tmp / (self.target.name + ".dlock")
            self.assertTrue(lock_file.exists())
        self.assertFalse(lock_file.exists())

    def test_cross_process_contention(self):
        # 模拟跨进程持有：手动创建锁文件，另一个实例应超时
        lock_file = self.tmp / (self.target.name + ".dlock")
        lock_file.write_text(str(time.time()))
        with self.assertRaises(FileLockTimeout):
            with file_lock(str(self.target), timeout=0.2, poll=0.02):
                pass


# ============ cache ============
class TestMemoryCache(unittest.TestCase):
    def test_ttl_expire(self):
        c = TTLCache(maxsize=10, ttl=0.1)
        c.put("k", "v")
        self.assertEqual(c.get("k"), "v")
        time.sleep(0.15)
        self.assertIsNone(c.get("k"))

    def test_lru_evict(self):
        c = TTLCache(maxsize=2, ttl=60)
        c.put("a", 1)
        c.put("b", 2)
        c.put("c", 3)
        self.assertIsNone(c.get("a"))
        self.assertIsNotNone(c.get("b"))
        self.assertIsNotNone(c.get("c"))

    def test_invalidate(self):
        c = TTLCache()
        c.put("k", 1)
        c.invalidate("k")
        self.assertIsNone(c.get("k"))

    def test_namespaced(self):
        c = NamespacedCache()
        c.put("ns1", "k", "v1")
        c.put("ns2", "k", "v2")
        self.assertEqual(c.get("ns1", "k"), "v1")
        self.assertEqual(c.get("ns2", "k"), "v2")
        c.invalidate("ns1")
        self.assertIsNone(c.get("ns1", "k"))
        self.assertEqual(c.get("ns2", "k"), "v2")


class TestLLMCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent_llm_cache_"))
        self.cache = LLMCache(cache_dir=self.tmp, ttl=60.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        msgs = [{"role": "user", "content": "1+1=?"}]
        self.assertIsNone(self.cache.get("m1", msgs, 0.1))
        self.cache.store("m1", msgs, 0.1, "2")
        self.assertEqual(self.cache.get("m1", msgs, 0.1), "2")

    def test_key_differentiates(self):
        msgs_a = [{"role": "user", "content": "a"}]
        msgs_b = [{"role": "user", "content": "b"}]
        self.cache.store("m", msgs_a, 0.1, "1")
        self.assertIsNone(self.cache.get("m", msgs_b, 0.1))
        self.assertIsNone(self.cache.get("m2", msgs_a, 0.1))

    def test_cleanup(self):
        self.cache.store("m", [{"role": "user", "content": "x"}], 0.0, "v")
        self.cache.store("m", [{"role": "user", "content": "y"}], 0.0, "v")
        c2 = LLMCache(cache_dir=self.tmp, ttl=0.0)  # 立即过期
        removed = c2.cleanup(max_age=0.0)
        self.assertGreaterEqual(removed, 1)

    def test_stats(self):
        msgs = [{"role": "user", "content": "s"}]
        self.cache.get("m", msgs, 0.1)   # miss
        self.cache.store("m", msgs, 0.1, "v")
        self.cache.get("m", msgs, 0.1)   # hit
        st = self.cache.stats()
        self.assertEqual(st["miss"], 1)
        self.assertEqual(st["hit"], 1)


# ============ model_router ============
class TestModelRouter(unittest.TestCase):
    def test_route_fallback(self):
        r = ModelRouter()
        # main 应回落 ollama.chat_model
        self.assertTrue(r.route("main"))
        # light 路由
        self.assertTrue(r.route("score"))
        self.assertTrue(r.route("summary"))

    def test_set_route(self):
        r = ModelRouter()
        r.set_route("test_type", "light")
        self.assertTrue(r.route("test_type"))

    def test_dump(self):
        r = ModelRouter()
        d = r.dump()
        self.assertIn("models", d)
        self.assertIn("routing", d)


# ============ context_compressor ============
class TestContextCompressor(unittest.TestCase):
    def setUp(self):
        self.c = ContextCompressor()

    def test_budget_light(self):
        self.assertEqual(self.c.budget_for("简短任务"), 4000)

    def test_budget_deep_long_task(self):
        self.assertEqual(self.c.budget_for("长" * 1000), 10000)

    def test_budget_deep_tool_calls(self):
        self.assertEqual(self.c.budget_for("x", tool_calls=8), 10000)

    def test_compress_short_messages_untouched(self):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"}]
        out = self.c.compress_messages(msgs, 6000)
        self.assertEqual(out, msgs)

    def test_compress_long_messages_keeps_system_and_recent(self):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"第{i}轮内容" + "很长的内容" * 50})
        out = self.c.compress_messages(msgs, 1000, keep_recent=2)
        # 应保留 system + 摘要 + 最近2条
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(out[-1], msgs[-1])
        self.assertLess(len(out), len(msgs))

    def test_summarize_short_observation(self):
        self.assertEqual(self.c.summarize_observation("短文本"), "短文本")


# ============ session_manager ============
class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionManager(idle_timeout=0.2)

    def test_register_active(self):
        sid = str(uuid.uuid4())
        self.mgr.register(sid)
        self.assertTrue(self.mgr.is_active(sid))
        self.assertEqual(len(self.mgr.active_sessions()), 1)

    def test_cleanup_idle(self):
        sid = str(uuid.uuid4())
        self.mgr.register(sid)
        time.sleep(0.3)
        victims = self.mgr.cleanup_idle()
        self.assertIn(sid, victims)
        self.assertFalse(self.mgr.is_active(sid))
        self.assertEqual(len(self.mgr.active_sessions()), 0)

    def test_heartbeat_keeps_alive(self):
        sid = str(uuid.uuid4())
        self.mgr.register(sid)
        time.sleep(0.15)
        self.mgr.heartbeat(sid)
        time.sleep(0.15)
        self.assertTrue(self.mgr.is_active(sid))  # 心跳续期后仍在活跃窗口

    def test_unregister(self):
        sid = str(uuid.uuid4())
        self.mgr.register(sid)
        self.mgr.unregister(sid)
        self.assertFalse(self.mgr.is_active(sid))


# ============ async_pool ============
class TestAsyncPool(unittest.TestCase):
    def test_submit(self):
        pool = AsyncPool(max_workers=2)
        fut = pool.submit(lambda: 42)
        self.assertEqual(fut.result(timeout=5), 42)

    def test_periodic(self):
        pool = AsyncPool(max_workers=2)
        counter = {"n": 0}
        def tick():
            counter["n"] += 1
        t = pool.schedule_periodic(tick, 0.1, "test-tick")
        time.sleep(0.35)
        pool.stop_periodic("test-tick")
        self.assertGreaterEqual(counter["n"], 2)
        pool.shutdown()


# ============ config paths ============
class TestConfigPaths(unittest.TestCase):
    def test_paths_absolute(self):
        self.assertTrue(Path(config_loader["sandbox"]["root_path"]).is_absolute())
        self.assertTrue(Path(config_loader["kb"]["persist_path"]).is_absolute())
        self.assertTrue(Path(config_loader["log"]["log_path"]).is_absolute())

    def test_resolve_path(self):
        r = resolve_path("./sandbox")
        p = Path(r)
        self.assertTrue(p.is_absolute())


# ============ thought_lib 思维架构库 ============
from core.thought_lib import ThoughtLib


class TestThoughtLib(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 指向真实 thought/ 目录（项目内固定区域）
        cls.lib = ThoughtLib()

    def test_list_not_empty(self):
        items = self.lib.list()
        self.assertGreaterEqual(len(items), 16)
        names = {it["name"] for it in items}
        self.assertIn("scientific_pipeline", names)
        self.assertIn("multi_world_representation", names)
        self.assertIn("calculus_thinking", names)
        self.assertIn("interpolation_fitting", names)

    def test_recommend_threshold_filters_noise(self):
        # 阈值（min_score=3）：真命中必须过，闲聊噪声必须被拦住
        hit = self.lib.recommend("假设昨天是明天，那么今天是星期五，问今天实际是星期几？", max_items=1)
        self.assertEqual(hit, ["multi_world_representation"])
        noise = self.lib.recommend("今天天气不错，随便聊聊", max_items=1)
        self.assertEqual(noise, [])

    def test_steps_extracts_numbered_lines(self):
        arch = self.lib.get("multi_world_representation")
        text = arch.steps(max_items=3)
        self.assertIn("1.", text)
        self.assertLessEqual(len(text.splitlines()), 3)

    def test_catalog_covers_all(self):
        cat = self.lib.catalog()
        lines = [l for l in cat.splitlines() if l.startswith("- ")]
        # 目录 = 库里全部条目 + 首行 none（"不使用"也是可选的一条，选了就是纯 ReAct）
        self.assertEqual(len(lines), len(self.lib.list()) + 1)
        self.assertTrue(lines[0].startswith("- none"))
        self.assertIn("multi_world_representation", cat)

    def test_small_model_skips_thought_injection(self):
        """<7B 不注入方法论（4B 实测：读到方法名 4 次、没写一次"采用"、原地打转 4 遍）。"""
        from main import _model_can_use_thought
        self.assertFalse(_model_can_use_thought("qwen3.5:4b"))
        self.assertTrue(_model_can_use_thought("deepseekR1Q7B:latest"))
        self.assertTrue(_model_can_use_thought("llama3.1:8b"))
        self.assertTrue(_model_can_use_thought("deepseek-chat"))  # 名字里没有 b 数 → 按支持处理

    def test_first_is_react(self):
        # ReAct 必须排第一（order=1）
        items = self.lib.list()
        self.assertEqual(items[0]["name"], "react_loop")

    def test_order_sequence(self):
        # 全部条目 order 应连续递增
        orders = [it["order"] for it in self.lib.list()]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(orders[0], 1)

    def test_get_existing(self):
        arch = self.lib.get("contradiction_classification")
        self.assertIsNotNone(arch)
        self.assertTrue(arch.title)
        self.assertTrue(arch.body.strip())

    def test_get_missing(self):
        self.assertIsNone(self.lib.get("no_such_arch"))

    def test_render_prompt(self):
        text = self.lib.render_prompt(["react_loop"])
        self.assertIn("ReAct", text)
        self.assertIn("FinalAnswer", text)

    def test_render_all(self):
        text = self.lib.render_prompt()
        self.assertIn("科研七步", text)

    def test_recommend_scientific(self):
        # 求证/科研类任务应推荐科学流水线或证伪/反思类方法论
        rec = self.lib.recommend("请证明勾股定理，并验证结论是否成立")
        self.assertTrue(rec, "科研类任务应有推荐")
        self.assertTrue(any(n in rec for n in
                            ("scientific_pipeline", "falsification_test", "reflect_verify")),
                        f"科研类推荐应命中方法论，实际: {rec}")

    def test_recommend_divide(self):
        # 工程/实现类任务应推荐分治/状态机类方法论
        rec = self.lib.recommend("把一个大型系统拆解成独立模块逐步实现")
        self.assertTrue(any(n in rec for n in
                            ("divide_and_conquer", "state_machine_modeling", "react_loop")),
                        f"工程类推荐应命中方法论，实际: {rec}")

    def test_recommend_empty(self):
        self.assertEqual(self.lib.recommend(""), [])
        self.assertEqual(self.lib.recommend("   "), [])


# ============ memory v2 记忆分区 ============
from core.memory.tfri import tfri_score, update_access_metadata
from core.memory.attention import rerank, partition_budget, normalize_sim, rank
from core.memory.link import add_sandbox_backlink, get_sandbox_backlinks


class TestMemoryV2(unittest.TestCase):
    def test_tfri_decay(self):
        old = tfri_score(8.0, access_count=0, created_ts=time.time() - 100 * 86400, decay_rate=0.02)
        fresh = tfri_score(8.0, access_count=0, created_ts=time.time(), decay_rate=0.02)
        self.assertLess(old, fresh)
        self.assertGreaterEqual(old, 0.0)

    def test_tfri_permanent(self):
        old = tfri_score(8.0, access_count=0, created_ts=time.time() - 3650 * 86400,
                         decay_rate=0.02, permanent=True)
        fresh = tfri_score(8.0, access_count=0, created_ts=time.time(),
                           decay_rate=0.02, permanent=True)
        self.assertAlmostEqual(old, fresh, places=6)

    def test_access_gain(self):
        low = tfri_score(6.0, access_count=0, created_ts=time.time())
        high = tfri_score(6.0, access_count=100, created_ts=time.time())
        self.assertGreater(high, low)

    def test_update_access_metadata(self):
        meta = {"access_count": 0}
        m2 = update_access_metadata(meta)
        self.assertEqual(m2["access_count"], 1)
        m3 = update_access_metadata(m2)
        self.assertEqual(m3["access_count"], 2)
        self.assertIn("last_access_ts", m3)

    def test_normalize_sim(self):
        self.assertGreater(normalize_sim(0.1), normalize_sim(1.0))
        self.assertAlmostEqual(normalize_sim(0.0), 1.0)

    def test_rank_finite(self):
        it = {"distance": 0.5, "metadata": {"base_score": 7.0, "access_count": 3,
                                            "created_ts": time.time(), "type": "fact"}}
        r = rank(it)
        self.assertTrue(isinstance(r, float) and r == r)  # 非 NaN

    def test_rerank(self):
        old_it = {"distance": 1.5, "metadata": {"base_score": 1.0, "access_count": 0,
                                                "created_ts": time.time() - 999 * 86400, "type": "episode"}}
        new_it = {"distance": 0.1, "metadata": {"base_score": 9.0, "access_count": 10,
                                                "created_ts": time.time(), "type": "reflection"}}
        out = rerank([old_it, new_it])
        self.assertEqual(out[0], new_it)

    def test_partition_budget(self):
        parts = partition_budget(10)
        self.assertEqual(parts["core"] + parts["cold"] + parts["archive"], 10)
        self.assertEqual(parts["archive"], 0)

    def test_backlink_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "node.md")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("# 节点\n内容\n")
            self.assertTrue(add_sandbox_backlink(fp, "mem-123"))
            links = get_sandbox_backlinks(fp)
            self.assertIn("mem-123", links)
            # 再写一条，backlink 应追加
            self.assertTrue(add_sandbox_backlink(fp, "mem-456"))
            links = get_sandbox_backlinks(fp)
            self.assertIn("mem-456", links)


# ============ prompt_engineer 提示词助手 ============
from services.prompt_engineer import PromptEngineer


class TestPromptEngineer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ptpl_")
        self.pe = PromptEngineer()
        self.pe._dir = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_get_delete(self):
        self.pe.save_template("面试", "请介绍一下你自己", style="正式")
        data = self.pe.get_template("面试")
        self.assertEqual(data["content"], "请介绍一下你自己")
        self.assertEqual(data["style"], "正式")
        self.assertTrue(self.pe.delete_template("面试"))
        self.assertIsNone(self.pe.get_template("面试"))

    def test_list_templates(self):
        self.pe.save_template("t1", "a")
        self.pe.save_template("t2", "b")
        names = {t["name"] for t in self.pe.list_templates()}
        self.assertEqual(names, {"t1", "t2"})

    def test_compare_format(self):
        text = self.pe.compare("原始", "优化", ["更清晰"])
        self.assertIn("原版", text)
        self.assertIn("优化版", text)
        self.assertIn("更清晰", text)

    def test_optimize_empty(self):
        r = self.pe.optimize("   ")
        self.assertFalse(r["success"])

    def test_submit_empty(self):
        r = self.pe.submit("")
        self.assertFalse(r["success"])


# ============ imagination 想象空间 ============
import core.imagination.node as inode
import core.imagination.snapshot as isnap
from core.imagination.validator import (check_undefined_symbols, find_dependency_cycle,
                                        classify_contradiction, validate, _compare_assertions)


class TestImagination(unittest.TestCase):
    def setUp(self):
        # 重定向到临时沙盘，避免污染真实 sandbox
        self.tmp = tempfile.mkdtemp(prefix="imgn_")
        self._old_sandbox = inode.SANDBOX
        inode.SANDBOX = self.tmp
        self.sid = "test-imag-001"

    def tearDown(self):
        inode.SANDBOX = self._old_sandbox
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_node_crud(self):
        nid = inode.create_node(self.sid, "第一步", "分析对象 X", node_type="analysis")
        self.assertTrue(inode.node_path(self.sid, nid))
        self.assertTrue(inode.write_node(self.sid, nid, "更新后的正文"))
        self.assertIn("更新后", inode.read_node(self.sid, nid))
        nodes = inode.list_nodes(self.sid)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["node_id"], nid)
        self.assertEqual(nodes[0]["type"], "analysis")

    def test_snapshot_rollback(self):
        nid = inode.create_node(self.sid, "草稿", "v1 内容")
        snap = isnap.snapshot(self.sid, "first")
        inode.write_node(self.sid, nid, "v2 内容被改坏了")
        self.assertTrue(isnap.rollback(self.sid, snap))
        self.assertIn("v1 内容", inode.read_node(self.sid, nid))

    def test_list_snapshots(self):
        isnap.snapshot(self.sid, "a")
        snaps = isnap.list_snapshots(self.sid)
        self.assertGreaterEqual(len(snaps), 1)

    def test_undefined_symbols(self):
        undef = check_undefined_symbols("T(A) = F(B) + 2\nX = 3", defined={"T", "F"})
        syms = {u["symbol"] for u in undef}
        self.assertIn("A", syms)
        self.assertIn("B", syms)
        self.assertIn("X", syms)

    def test_dependency_cycle(self):
        steps = [
            {"id": "a", "requires": ["b"]},
            {"id": "b", "requires": ["c"]},
            {"id": "c", "requires": ["a"]},
        ]
        cycle = find_dependency_cycle(steps)
        self.assertTrue(cycle)

    def test_no_cycle(self):
        steps = [
            {"id": "a", "requires": []},
            {"id": "b", "requires": ["a"]},
            {"id": "c", "requires": ["b"]},
        ]
        self.assertEqual(find_dependency_cycle(steps), [])

    def test_contradiction_classify(self):
        self.assertEqual(classify_contradiction("x", can_reresent=True), "representation_conflict")
        self.assertEqual(classify_contradiction("x", bridge_mismatch=True), "bridge_conflict")
        self.assertEqual(classify_contradiction("x"), "logical_contradiction")

    def test_assertion_compare_numeric(self):
        kind, detail = _compare_assertions({"text": "X = 3"}, {"text": "X = 5"})
        self.assertEqual(kind, "logical_contradiction")

    def test_assertion_compare_direction(self):
        kind, detail = _compare_assertions({"text": "A 是 蓝色"}, {"text": "A 不是 蓝色"})
        self.assertEqual(kind, "logical_contradiction")

    def test_validate_ok(self):
        nodes = [{"id": "n1", "title": "结论", "content": "P = 3，结果成立",
                  "requires": []}]
        r = validate(self.sid, nodes, defined_symbols={"P"})
        self.assertTrue(r["ok"])


# ============ knowledge 冲突检测 ============
from services.knowledge_service import (_compare_texts, CONFLICT_OK,
                                        CONFLICT_COMPLEMENT, CONFLICT_CONFLICT,
                                        KnowledgeService)


class TestKnowledgeConflict(unittest.TestCase):
    def test_numeric_conflict(self):
        status, detail = _compare_texts("汇率 = 7.2", "汇率 = 7.5")
        self.assertEqual(status, CONFLICT_CONFLICT)

    def test_direction_conflict(self):
        status, detail = _compare_texts("模型 A 支持 GPU", "模型 A 不支持 GPU")
        self.assertEqual(status, CONFLICT_CONFLICT)

    def test_duplicate(self):
        status, detail = _compare_texts("Python 是解释型语言", "Python是解释型语言。")
        self.assertEqual(status, CONFLICT_OK)

    def test_complement(self):
        status, detail = _compare_texts("Python 适合数据分析", "Python 有丰富包生态")
        self.assertEqual(status, CONFLICT_COMPLEMENT)

    def test_resolve_kept_old(self):
        ks = KnowledgeService()
        r = ks.resolve_conflict("新知识", keep_new=False)
        self.assertEqual(r["action"], "kept_old")

    def test_resolve_kept_new_adds_doc(self):
        # P2 起「采纳新知识」会真正入库（此前 doc_id=None 时什么都不做）
        ks = KnowledgeService()
        ks.add_knowledge = lambda text, metadata=None, coll="knowledge_fact": "doc-x"
        r = ks.resolve_conflict("新知识", keep_new=True, doc_id=None)
        self.assertEqual(r["action"], "kept_new")
        self.assertEqual(r["doc_id"], "doc-x")


# ============ desktop sqlite 持久队列 ============
import core.desktop.queue as dq


class TestDesktopQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dq_")
        self._old_db = dq.DB_PATH
        dq.DB_PATH = os.path.join(self.tmp, ".queue.db")
        self.q = dq.DesktopQueue()

    def tearDown(self):
        dq.DB_PATH = self._old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enqueue_list(self):
        aid = self.q.enqueue("s1", "click", {"x": 10, "y": 20})
        items = self.q.list_queue(sid="s1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["action_type"], "click")

    def test_session_isolation(self):
        self.q.enqueue("s1", "click", {})
        self.q.enqueue("s2", "type_text", {"text": "hi"})
        self.assertEqual(len(self.q.list_queue(sid="s1")), 1)
        self.assertEqual(len(self.q.list_queue(sid="s2")), 1)

    def test_get_and_update(self):
        aid = self.q.enqueue("s1", "click", {})
        it = self.q.get(aid)
        self.assertIsNotNone(it)
        self.assertTrue(self.q.update_status(aid, "done"))
        self.assertEqual(self.q.get(aid)["status"], "done")

    def test_auto_reject_timeout(self):
        aid = self.q.enqueue("s1", "click", {}, timeout_sec=0.1)
        time.sleep(0.2)
        n = self.q.auto_reject_timeout("s1")
        self.assertEqual(n, 1)
        self.assertEqual(self.q.get(aid)["status"], "rejected")

    def test_to_global_view(self):
        self.q.enqueue("s1", "click", {})
        view = self.q.to_global_view()
        self.assertIn("s1", view)
        self.assertEqual(len(view["s1"]), 1)


# ============ vision 置信度 / 切片 / 稳定 ============
from core.vision.confidence import (jaccard_similarity, task_coverage,
                                    evaluate_confidence, route_by_confidence)
from core.vision.slicer import slice_image, merge_slice_texts

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


class TestVisionConfidence(unittest.TestCase):
    def test_jaccard(self):
        self.assertGreater(jaccard_similarity("猫在沙发上", "猫在沙发上睡觉"), 0.5)
        self.assertLess(jaccard_similarity("猫在沙发上", "天空中有飞机"), 0.3)

    def test_task_coverage(self):
        cov = task_coverage("识别到按钮 确定 和 取消", "找出确定和取消按钮")
        self.assertGreater(cov, 0.5)

    def test_evaluate_high(self):
        r = evaluate_confidence("确定 取消 保存", "确定取消保存",
                                second_sample="确定 取消 保存")
        self.assertEqual(r["level"], "high")

    def test_evaluate_low_when_mismatch(self):
        r = evaluate_confidence("随机文字", "特定目标",
                                second_sample="完全不同的内容")
        self.assertIn(r["level"], ("low", "medium"))

    def test_route(self):
        self.assertIn("沙盘", route_by_confidence("high"))
        self.assertIn("待核验", route_by_confidence("medium"))


@unittest.skipUnless(_HAS_PIL, "PIL 不可用")
class TestVisionSlicer(unittest.TestCase):
    def test_slice_long_image(self):
        with tempfile.TemporaryDirectory() as td:
            img_path = os.path.join(td, "long.png")
            Image.new("RGB", (400, 3000), (200, 200, 200)).save(img_path)
            slices = slice_image(img_path, slice_height=1024, overlap_ratio=0.7)
            self.assertGreaterEqual(len(slices), 3)
            # 切片间应有重叠（去重前提）
            self.assertGreater(slices[0]["bottom"], slices[1]["top"])

    def test_merge_dedup(self):
        parts = ["第一行\n第二行\n", "第二行\n第三行\n"]
        merged = merge_slice_texts(parts)
        self.assertIn("第一行", merged)
        self.assertIn("第三行", merged)
        self.assertEqual(merged.count("第二行"), 1)


@unittest.skipUnless(_HAS_PIL, "PIL 不可用")
class TestVisionStability(unittest.TestCase):
    def test_black_screen(self):
        from core.vision.stability import is_black_screen
        with tempfile.TemporaryDirectory() as td:
            black = os.path.join(td, "black.png")
            Image.new("RGB", (200, 200), (0, 0, 0)).save(black)
            is_black, _ = is_black_screen(black)
            self.assertTrue(is_black)

    def test_same_frame_stable(self):
        from core.vision.stability import is_stable
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "f.png")
            Image.new("RGB", (100, 100), (128, 128, 128)).save(fp)
            stable, diff = is_stable(fp, fp)
            self.assertTrue(stable)
            self.assertLess(diff, 0.02)


# ============ ACL 动作级 ============
from core.vision_acl_mgr import vision_acl_mgr


class TestACLPathActions(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = vision_acl_mgr.cfg
        # 关键：add_path_action 内部会 save() 落盘。若不禁用，跑一次测试就把真实
        # config/vision_acl.json 覆盖成这里的临时配置（白名单变临时目录、workspace 段消失）
        self._orig_save = vision_acl_mgr.save
        vision_acl_mgr.save = lambda: None
        vision_acl_mgr.cfg = {
            "enable_acl": True,
            "default_deny": True,
            "whitelist": [os.getcwd()],
            "rules": {"allow_mouse_keyboard": False, "allow_launch_app": True,
                      "allow_read_file": True, "allow_write_file": False,
                      "allow_folder_scan": True},
            "path_actions": {},
        }

    def tearDown(self):
        vision_acl_mgr.cfg = self._orig_cfg
        vision_acl_mgr.save = self._orig_save

    def test_path_action_overrides(self):
        import tempfile as tf
        with tf.TemporaryDirectory() as td:
            vision_acl_mgr.cfg["whitelist"] = [td]
            vision_acl_mgr.add_path_action(td, ["read_file"])
            # 动作级允许 read_file，即使全局规则只读也不冲突
            permit, reason = vision_acl_mgr.check_action_permit(td, "read_file")
            self.assertTrue(permit)
            # 动作级未允许 mouse_keyboard → 拒绝（不回落全局 False）
            permit2, _ = vision_acl_mgr.check_action_permit(td, "mouse_keyboard")
            self.assertFalse(permit2)
            # 清理（内存 cfg，不落盘）
            vision_acl_mgr.remove_path_action(td)

    def test_fallback_global_rule(self):
        import tempfile as tf
        with tf.TemporaryDirectory() as td:
            vision_acl_mgr.cfg["whitelist"] = [td]
            vision_acl_mgr.cfg["path_actions"] = {}
            permit, reason = vision_acl_mgr.check_action_permit(td, "launch_app")
            self.assertTrue(permit)  # 全局 allow_launch_app=True


class TestWorkspaceACL(unittest.TestCase):
    """视觉工作台放行软件：名单为空=默认放行，填了就只认名单"""

    def setUp(self):
        import copy
        self._orig_cfg = vision_acl_mgr.cfg
        self._orig_save = vision_acl_mgr.save
        vision_acl_mgr.save = lambda: None
        vision_acl_mgr.cfg = copy.deepcopy(vision_acl_mgr.cfg)
        vision_acl_mgr.cfg["enable_acl"] = True
        vision_acl_mgr.cfg["workspace"] = {"enabled": True, "allow_software": []}

    def tearDown(self):
        vision_acl_mgr.cfg = self._orig_cfg
        vision_acl_mgr.save = self._orig_save

    def test_default_cfg_has_workspace_section(self):
        from core.vision_acl_mgr import DEFAULT_CFG
        self.assertIn("workspace", DEFAULT_CFG)
        self.assertIn("allow_software", DEFAULT_CFG["workspace"])

    def test_empty_allow_means_pass(self):
        ok, why = vision_acl_mgr.check_workspace_software("画图")
        self.assertTrue(ok)

    def test_allow_list_is_exclusive(self):
        vision_acl_mgr.set_workspace_allow(["画图"])
        self.assertTrue(vision_acl_mgr.check_workspace_software("画图")[0])
        ok, why = vision_acl_mgr.check_workspace_software("记事本")
        self.assertFalse(ok)
        self.assertIn("不在工作台放行名单", why)

    def test_acl_off_fails_open(self):
        vision_acl_mgr.cfg["enable_acl"] = False
        vision_acl_mgr.set_workspace_allow(["画图"])
        self.assertTrue(vision_acl_mgr.check_workspace_software("记事本")[0])

    def test_load_merges_default_sections(self):
        # 配置文件缺 workspace 段时要按默认补齐，不能让新能力静默失效
        import core.vision_acl_mgr as vam
        _orig_path = vam.ACL_PATH
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "acl.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"enable_acl": True, "whitelist": [],
                           "rules": {"allow_read_file": True}}, f)
            vam.ACL_PATH = Path(p)
            try:
                m = vam.VisionACLManager()
                self.assertIn("workspace", m.cfg)
                self.assertIn("path_actions", m.cfg)
                self.assertTrue(m.cfg["rules"]["allow_launch_app"])  # 默认补齐
            finally:
                vam.ACL_PATH = _orig_path


# ============ P3 crew / debate / toolchain / plugin / research / mode ============
import core.crew as crew_mod
import core.debate as debate_mod
import core.debate.extractor as extractor_mod
from core.toolchain import list_chains, run_toolchain, chains_desc
from core.plugin import registry as _registry
import core.research.pipeline as rp
import core.mode.strategy as mstrategy
import core.mode.permission as mperm
import core.mode.risk_monitor as mrisk


class _FakeResp:
    def __init__(self, content, success=True):
        self.content = content
        self.success = success


class TestCrew(unittest.TestCase):
    def test_spawn_and_status(self):
        old = crew_mod.llm_client.chat
        crew_mod.llm_client.chat = lambda *a, **k: _FakeResp("子任务结论")
        try:
            sub_id = crew_mod.spawn_subagent(crew_mod.SubAgentSpec(
                role="researcher", prompt="调研", sandbox_node="t.md"))
            st = crew_mod.get_status(sub_id)
            self.assertIsNotNone(st)
            self.assertEqual(st["role"], "researcher")
        finally:
            crew_mod.llm_client.chat = old

    def test_list_and_cleanup(self):
        self.assertIsInstance(crew_mod.list_agents(), list)
        n = crew_mod.cleanup_agents(older_than_sec=0)
        self.assertGreaterEqual(n, 0)


class TestDebate(unittest.TestCase):
    def test_scheduler_basic(self):
        did = debate_mod.start_debate("测试辩论", roles=[
            {"name": "proposer", "quota": 3}, {"name": "critic", "quota": 3}])
        self.assertTrue(debate_mod.add_turn(did, "proposer", "A 是正确的"))
        self.assertTrue(debate_mod.add_turn(did, "critic", "不同意，B 才是"))
        s = debate_mod.get_session(did)
        self.assertEqual(len(s.turns), 2)
        self.assertGreater(debate_mod.consensus_score(s.turns, s.topic), 0)

    def test_converged_state(self):
        did = debate_mod.start_debate("t")
        for i in range(8):
            debate_mod.add_turn(did, "proposer", f"结论X正确（第{i}次确认）")
            debate_mod.add_turn(did, "critic", "同意 结论X正确")
        s = debate_mod.get_session(did)
        self.assertTrue(debate_mod.check_converged(s))

    def test_extract_patched(self):
        old = extractor_mod.llm_client.chat
        extractor_mod.llm_client.chat = lambda *a, **k: _FakeResp(
            '{"consensus": ["一致"], "disagreements": [], "open_questions": ["Q1"]}')
        try:
            did = debate_mod.start_debate("t")
            debate_mod.add_turn(did, "proposer", "P")
            r = debate_mod.extract(debate_mod.get_session(did))
            self.assertIn("consensus", r)
            self.assertEqual(r["consensus"], ["一致"])
        finally:
            extractor_mod.llm_client.chat = old


class TestToolchain(unittest.TestCase):
    def test_builtin_chains(self):
        chains = list_chains()
        names = {c["name"] for c in chains}
        self.assertIn("web_research", names)
        self.assertNotIn("hypothesis_verify", names)   # 猜想链已于 2026-11-08 移除
        self.assertIn("software_learn", names)

    def test_run_with_fake_map(self):
        fake_map = {"browser_fetch": {"desc": "", "func": lambda **k: "页面内容"},
                    "llm_discuss": {"desc": "", "func": lambda **k: "总结要点"}}
        r = run_toolchain("web_research", {"url": "u", "query": "q"}, "s", tool_map=fake_map)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.step_results), 2)

    def test_run_unknown(self):
        r = run_toolchain("no_such_chain", {}, "s", tool_map={})
        self.assertFalse(r.ok)

    def test_chains_desc(self):
        self.assertIn("web_research", chains_desc())


class TestPluginRegistry(unittest.TestCase):
    def test_register_and_call(self):
        _registry.register_builtin("test_p", {"fake_tool": {"desc": "测试", "func": lambda **k: "ok"}})
        spec = _registry.get_tool("fake_tool")
        self.assertIsNotNone(spec)
        self.assertEqual(_registry.call("fake_tool", {}), "ok")
        self.assertIn("fake_tool", _registry.all_tools())

    def test_call_error_handling(self):
        _registry.register_builtin("test_p2", {"boom": {"desc": "e", "func": lambda **k: 1 / 0}})
        out = _registry.call("boom", {})
        self.assertIn("异常", out)

    def test_disable_removes_tool(self):
        _registry.register_builtin("test_p3", {"gone_tool": {"desc": "g", "func": lambda **k: ""}})
        _registry.disable("test_p3")
        self.assertIsNone(_registry.get_tool("gone_tool"))
        _registry.enable("test_p3")


class TestResearch(unittest.TestCase):
    def test_create_and_list(self):
        r = rp.create_research("测试课题")
        self.assertIn("topic_id", r)
        self.assertEqual(r["step"], 1)
        rows = rp.list_research()
        self.assertTrue(any(x["topic_id"] == r["topic_id"] for x in rows))



class TestMode(unittest.TestCase):
    def test_predict_default_steady(self):
        ms = mstrategy.ModeStrategy()
        self.assertEqual(ms.predict_mode("任意任务"), "steady")

    def test_manual_override(self):
        ms = mstrategy.ModeStrategy()
        ms.set_manual_mode("inquiry")
        self.assertEqual(ms.predict_mode(""), "inquiry")
        ms.clear_manual_mode()

    def test_permission_matrix(self):
        allow, reason = mperm.check("steady", "debate_start")
        self.assertFalse(allow)
        allow2, _ = mperm.check("inquiry", "debate_start")
        self.assertTrue(allow2)
        allow3, _ = mperm.check("steady", "scan_folder")
        self.assertTrue(allow3)

    def test_risk_monitor(self):
        allow, reason = mrisk.pre_check("steady", "desktop_action", {"action": "click", "x": 1})
        self.assertFalse(allow)   # steady 下点击风险拦截
        allow2, _ = mrisk.pre_check("inquiry", "desktop_action", {"action": "click"})
        self.assertTrue(allow2)
        allow3, _ = mrisk.pre_check("steady", "vl_analyze_image", {})
        self.assertTrue(allow3)


# ============ P4 热加载 / tutor / obsidian ============
from core.plugin.loader import PluginHotLoader
import services.tutor_service as tutor_mod
from core.obsidian_adapter import export_sandbox, export_all, build_index
import core.obsidian_adapter as obs_adapter


class TestPluginHotLoader(unittest.TestCase):
    def test_discovers_sample_plugin(self):
        hl = PluginHotLoader(roots=["E:/agent_project/plugins/installed",
                                    "E:/agent_project/plugins/market"],
                             interval=2.0)
        n = hl.scan_once()
        # 首次扫描应发现 calc_plugin / sample_market_plugin（或已注册返回0）
        from core.plugin import registry
        spec = registry.get_tool("calc")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.func("2+3*4"), "2+3*4 = 14")

    def test_scan_again_stable(self):
        hl = PluginHotLoader(roots=["E:/agent_project/plugins/installed"])
        hl.scan_once()
        n2 = hl.scan_once()   # 无变更应为 0
        self.assertEqual(n2, 0)


class TestTutor(unittest.TestCase):
    def test_ask_patched(self):
        old = tutor_mod.llm_client.chat
        tutor_mod.llm_client.chat = lambda *a, **k: _FakeResp("第一步：…第二步：…")
        try:
            out = tutor_mod.tutor_service.ask("如何入门Python？")
            self.assertIn("第一步", out)
        finally:
            tutor_mod.llm_client.chat = old

    def test_teach_patched(self):
        old = tutor_mod.llm_client.chat
        tutor_mod.llm_client.chat = lambda *a, **k: _FakeResp('{"title": "T", "sections": []}')
        try:
            out = tutor_mod.tutor_service.teach("微积分")
            self.assertIn("sections", out)
        finally:
            tutor_mod.llm_client.chat = old


class TestObsidianAdapter(unittest.TestCase):
    def test_export_sandbox(self):
        with tempfile.TemporaryDirectory() as vault, tempfile.TemporaryDirectory() as sb:
            sid = "obs_test"
            os.makedirs(os.path.join(sb, sid))
            with open(os.path.join(sb, sid, "note.md"), "w", encoding="utf-8") as f:
                f.write("# 测试笔记\n内容")
            r = export_sandbox(sid, vault, sandbox_root=sb)
            self.assertTrue(r["ok"])
            self.assertEqual(r["exported"], 1)
            self.assertTrue(os.path.exists(os.path.join(vault, "agent_sandbox", sid, "note.md")))

    def test_export_all_and_index(self):
        with tempfile.TemporaryDirectory() as vault, tempfile.TemporaryDirectory() as sb:
            sid1 = "s1"
            os.makedirs(os.path.join(sb, sid1))
            with open(os.path.join(sb, sid1, "a.md"), "w", encoding="utf-8") as f:
                f.write("a")
            results = export_all(vault, sandbox_root=sb)
            self.assertGreaterEqual(len(results), 1)
            idx = build_index(vault)
            self.assertTrue(os.path.exists(idx))
            with open(idx, encoding="utf-8") as f:
                self.assertIn("s1", f.read())


# ============ 并发安全 ============
import threading as _threading


class TestConcurrency(unittest.TestCase):
    def test_registry_thread_safety(self):
        """多线程并发 get_tool/all_tools/call 不抛异常、结果正确"""
        errors = []

        def worker():
            try:
                for _ in range(40):
                    _registry.all_tools()
                    spec = _registry.get_tool("calc")
                    if spec is None:
                        errors.append("calc 丢失")
                        return
                    out = _registry.call("calc", {"expr": "1+2"})
                    if "3" not in out:
                        errors.append(f"calc 结果异常: {out}")
                        return
            except Exception as e:
                errors.append(e)

        threads = [_threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_desktop_queue_thread_safety(self):
        from core.desktop.queue import desktop_queue
        errors = []

        def w():
            try:
                for i in range(20):
                    aid = desktop_queue.enqueue("t_sid", "open_file", {"path": "x"})
                    desktop_queue.get(aid)
            except Exception as e:
                errors.append(e)

        threads = [_threading.Thread(target=w) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_maybe_compact(self):
        """上下文压缩守护：超阈值时压缩中间历史，保留首尾"""
        import main as _m
        msgs = [{"role": "system", "content": "S" * 100},
                {"role": "user", "content": "召回" * 2000},
                {"role": "assistant", "content": "A" * 2000},
                {"role": "user", "content": "B" * 2000},
                {"role": "assistant", "content": "C" * 500},
                {"role": "user", "content": "D" * 500}]
        changed = _m._maybe_compact(msgs, threshold=6000, keep_recent=4)
        self.assertTrue(changed)
        self.assertTrue(any("历史轮次摘要" in m.get("content", "") for m in msgs))
        # 未超阈值不压缩
        small = [{"role": "user", "content": "短"}] * 3
        self.assertFalse(_m._maybe_compact(small, threshold=6000))


# ============ 会话清理 / 结论沉淀 ============
from core.session_manager import SessionManager, SANDBOX_ROOT as SESSION_SANDBOX_ROOT


class TestSessionCleanup(unittest.TestCase):
    def test_cleanup_idle_victims_and_data(self):
        sm = SessionManager(idle_timeout=0.2)
        sid = "idle_sess_test"
        os.makedirs(os.path.join(SESSION_SANDBOX_ROOT, sid))
        sm.register(sid)
        self.assertTrue(sm.is_active(sid))
        time.sleep(0.3)
        victims = sm.cleanup_idle(timeout=0.2)
        self.assertIn(sid, victims)
        self.assertFalse(sm.is_active(sid))
        # 沙盘目录应被清理
        self.assertFalse(os.path.exists(os.path.join(SESSION_SANDBOX_ROOT, sid)))

    def test_cleanup_keeps_active(self):
        sm = SessionManager(idle_timeout=60.0)
        sm.register("keep_me")
        victims = sm.cleanup_idle(timeout=60.0)
        self.assertNotIn("keep_me", victims)
        sm.unregister("keep_me")


class TestSinkConclusion(unittest.TestCase):
    def test_short_final_skipped(self):
        import main as _m
        self.assertIsNone(_m._sink_conclusion("任务", "太短"))

    def test_sink_uses_knowledge_safe(self):
        import main as _m
        calls = []

        class _FakeKS:
            def add_knowledge_safe(self, text, metadata=None, coll=None, auto_resolve=None):
                calls.append((text, coll))
                return {"action": "added", "doc_id": "x"}

        old = _m.knowledge_service
        _m.knowledge_service = _FakeKS()
        try:
            r = _m._sink_conclusion("计算2+2", "结果是4，因为加法是最基本的算术运算之一，两个相同数量的集合合并后总量翻倍，这是群论中结合律与幺元性质的直观体现")
            self.assertIsNotNone(r)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], "knowledge_fact")
        finally:
            _m.knowledge_service = old


class TestConfigSavePreserve(unittest.TestCase):
    """save_config 必须保留未托管段（session/idle/vision）且列表类型不变"""

    def setUp(self):
        from core.config_loader import CONFIG_PATH
        import tomllib
        self._cfg_path = CONFIG_PATH
        self._orig = CONFIG_PATH.read_text(encoding="utf-8")
        self._before = tomllib.loads(self._orig)

    def tearDown(self):
        self._cfg_path.write_text(self._orig, encoding="utf-8")

    def test_extra_sections_preserved(self):
        import tomllib
        from core.config_loader import save_config
        save_config()
        after = tomllib.loads(self._cfg_path.read_text(encoding="utf-8"))
        for sec in ("session", "idle"):
            self.assertIn(sec, after, f"段 [{sec}] 被 save_config 丢弃")
            self.assertEqual(after[sec], self._before.get(sec), f"段 [{sec}] 内容被改写")

    def test_vision_section_removed(self):
        """权限单源后 [vision.*] 不应再被 save_config 复活"""
        import tomllib
        from core.config_loader import save_config
        save_config()
        after = tomllib.loads(self._cfg_path.read_text(encoding="utf-8"))
        self.assertNotIn("vision", after)


# ============ R12：闲聊上下文 / 摘要指针 / 后台任务 ============
class TestChatIdentityContext(unittest.TestCase):
    """R6：每个模型一份 system、历史按说话人拆分（自己的 → assistant）"""

    def setUp(self):
        from services import agent_chat as ac
        self.ac = ac
        # 测试隔离：真实长聊进程可能正持轮次锁，这里强制放行
        self._old_acquire = ac._acquire_round_lock
        self._old_release = ac._release_round_lock
        ac._acquire_round_lock = lambda: True
        ac._release_round_lock = lambda: None
        self.tmp = tempfile.mkdtemp(prefix="chat_ctx_")
        self._old_root = ac.CHAT_ROOT
        self._old_sum = ac.SUMMARY_FILE
        ac.CHAT_ROOT = self.tmp
        ac.SUMMARY_FILE = os.path.join(self.tmp, "chat_summary.json")
        with open(os.path.join(self.tmp, "chat.md"), "w", encoding="utf-8") as f:
            f.write("2026-11-05 22:00 | 豆包：今天聊七层记忆\n"
                    "2026-11-05 22:01 | Llama：我觉得可以\n"
                    "2026-11-05 22:02 | 小深：先修锁\n"
                    "2026-11-05 22:03 | Llama：同意先修锁\n")
        with open(ac.SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump({"line": 4, "summary": "旧摘要", "long_summary": "长摘要",
                       "long_line": 4}, f, ensure_ascii=False)

    def tearDown(self):
        self.ac.CHAT_ROOT = self._old_root
        self.ac.SUMMARY_FILE = self._old_sum
        self.ac._acquire_round_lock = self._old_acquire
        self.ac._release_round_lock = self._old_release
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identity_system_has_own_name(self):
        sysmsg = self.ac.chat_space._identity_system("llama3.1:8b", "chat", "")
        self.assertIn("【你的身份】", sysmsg)
        self.assertIn("Llama", sysmsg)
        self.assertIn("你自己说过的话", sysmsg)

    def test_history_split_by_speaker(self):
        hist = self.ac.chat_space._recent(15)
        msgs = self.ac.chat_space._history_messages(hist, "Llama")
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
        self.assertTrue(msgs[0]["content"].startswith("豆包："))
        self.assertIn("我觉得可以", msgs[1]["content"])   # 自己的话 → assistant

    def test_round_prompt_is_identity_aware(self):
        captured = []

        class _R:
            success = True
            content = "同意，先修锁再聊别的。"

        def _fake(messages, **kw):
            captured.append(messages)
            return _R()

        old_chat, old_openai = self.ac.llm_client.chat, self.ac.llm_client.chat_openai
        old_participants = self.ac._participants
        try:
            self.ac.llm_client.chat = _fake
            self.ac.llm_client.chat_openai = _fake
            self.ac._participants = lambda: ["llama3.1:8b"]
            res = self.ac.chat_space.chat_round(mode="chat")
        finally:
            self.ac.llm_client.chat = old_chat
            self.ac.llm_client.chat_openai = old_openai
            self.ac._participants = old_participants

        self.assertTrue(res["ok"])
        self.assertEqual(len(captured), 1)
        msgs = captured[0]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Llama", msgs[0]["content"])
        self.assertTrue(any(m["role"] == "assistant" for m in msgs))
        self.assertTrue(any(m["role"] == "user" and m["content"].startswith("豆包：")
                            for m in msgs))
        # 发言已落盘
        with open(os.path.join(self.tmp, "chat.md"), encoding="utf-8") as f:
            self.assertEqual(len([l for l in f.read().splitlines() if l.strip()]), 5)

    def test_busy_when_lock_held(self):
        """轮次锁被占时 chat_round 返回 busy，而不是并发跑第二轮"""
        self.ac._acquire_round_lock = lambda: False
        res = self.ac.chat_space.chat_round(mode="chat")
        self.assertTrue(res.get("busy"))
        self.assertFalse(res.get("ok"))


class TestChatSummaryPointer(unittest.TestCase):
    """R5：摘要按行消费，不再「截尾 3000 字却把指针推到 total」"""

    def setUp(self):
        from services import agent_chat as ac
        self.ac = ac
        self.tmp = tempfile.mkdtemp(prefix="chat_sum_")
        self._old_root = ac.CHAT_ROOT
        self._old_sum = ac.SUMMARY_FILE
        ac.CHAT_ROOT = self.tmp
        ac.SUMMARY_FILE = os.path.join(self.tmp, "chat_summary.json")
        with open(os.path.join(self.tmp, "chat.md"), "w", encoding="utf-8") as f:
            for i in range(60):
                f.write(f"2026-11-05 22:{i:02d} | 小深：第{i}条发言\n")
        self._write_state({"line": 0, "summary": "", "long_summary": "", "long_line": 0})
        self._old_chat = ac.llm_client.chat

        class _R:
            success = True
            content = "假摘要：保留了话题与约定。"

        ac.llm_client.chat = lambda *a, **k: _R()

    def _write_state(self, st):
        with open(self.ac.SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)

    def _read_state(self):
        with open(self.ac.SUMMARY_FILE, encoding="utf-8") as f:
            return json.load(f)

    def tearDown(self):
        self.ac.CHAT_ROOT = self._old_root
        self.ac.SUMMARY_FILE = self._old_sum
        self.ac.llm_client.chat = self._old_chat
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_short_summary_advances_by_lines(self):
        r = self.ac.chat_space.maybe_summarize(force=True)
        self.assertTrue(r.get("ok"))
        self.assertEqual(self._read_state()["line"], 40)   # 不是 60
        r2 = self.ac.chat_space.maybe_summarize(force=True)
        self.assertTrue(r2.get("ok"))
        self.assertEqual(self._read_state()["line"], 60)

    def test_long_summary_advances_by_lines(self):
        r = self.ac.chat_space.maybe_long_summarize(force=True)
        self.assertTrue(r.get("ok"))
        self.assertEqual(self._read_state()["long_line"], 50)

    def test_not_enough_new_lines(self):
        st = self._read_state()
        st["line"] = 55
        self._write_state(st)
        r = self.ac.chat_space.maybe_summarize(force=False)
        self.assertFalse(r.get("ok"))
        self.assertEqual(self._read_state()["line"], 55)


class TestBgJobs(unittest.TestCase):
    """R13：后台任务并发/异常/状态"""

    def setUp(self):
        from services import bg_jobs
        self.bg = bg_jobs
        bg_jobs.reset()

    def tearDown(self):
        time.sleep(0.1)

    def test_run_and_result(self):
        ok = self.bg.start("测试", lambda: (time.sleep(0.3), "done")[1])
        self.assertTrue(ok)
        self.assertTrue(self.bg.snapshot()["running"])
        self.assertFalse(self.bg.start("第二个", lambda: "x"))  # 同时只允许一个
        time.sleep(0.6)
        snap = self.bg.snapshot()
        self.assertFalse(snap["running"])
        self.assertEqual(snap["result"], "done")

    def test_exception_captured(self):
        def boom():
            raise RuntimeError("后台炸了")

        self.assertTrue(self.bg.start("异常", boom))
        time.sleep(0.3)
        snap = self.bg.snapshot()
        self.assertFalse(snap["running"])
        self.assertIn("后台炸了", snap["error"])


class TestLockedAppend(unittest.TestCase):
    """R1：chat.md 单写者——多线程并发追加不丢行"""

    def test_threaded_append(self):
        from services.agent_chat import _locked_append
        tmp = tempfile.mkdtemp(prefix="append_")
        target = os.path.join(tmp, "chat.md")
        try:
            def worker(tag):
                for i in range(25):
                    _locked_append(target, f"{tag}-{i:02d}")

            threads = [threading.Thread(target=worker, args=(f"t{k}",)) for k in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with open(target, encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            self.assertEqual(len(lines), 100)
            self.assertEqual(len(set(lines)), 100)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLongChatInstanceLock(unittest.TestCase):
    """R1：长聊实例锁——同一文件第二次加锁失败，释放后成功"""

    def test_byte_lock(self):
        import msvcrt
        from services import long_chat as lc
        tmp = tempfile.mkdtemp(prefix="lock_")
        p = os.path.join(tmp, "x.lock")
        fd1 = os.open(p, os.O_RDWR | os.O_CREAT)
        fd2 = os.open(p, os.O_RDWR | os.O_CREAT)
        try:
            self.assertTrue(lc._try_bytes_lock(fd1))
            self.assertFalse(lc._try_bytes_lock(fd2))
            os.lseek(fd1, 0, 0)
            msvcrt.locking(fd1, msvcrt.LK_UNLCK, 1)
            self.assertTrue(lc._try_bytes_lock(fd2))
        finally:
            for fd in (fd1, fd2):
                try:
                    os.close(fd)
                except OSError:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)



# ============ P2：待仲裁 / 访问计数 / 模板 CRUD ============
class TestKnowledgePending(unittest.TestCase):
    def setUp(self):
        from services import knowledge_service as ks
        self.ks_mod = ks
        self.tmp = tempfile.mkdtemp(prefix="kn_pending_")
        self._old = ks.PENDING_FILE
        ks.PENDING_FILE = Path(self.tmp) / "pending.json"
        self.svc = ks.KnowledgeService()
        self._old_add = self.svc.add_knowledge

    def tearDown(self):
        self.ks_mod.PENDING_FILE = self._old
        self.svc.add_knowledge = self._old_add
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_queue_dedup_and_resolve(self):
        self.svc.queue_pending("新知识 A", {"source": "t"}, "conflict", "冲突")
        self.svc.queue_pending("新知识 A", {"source": "t"}, "conflict", "冲突")
        self.assertEqual(len(self.svc.list_pending()), 1)
        self.svc.add_knowledge = lambda text, metadata=None, coll="knowledge_fact": "doc-1"
        r = self.svc.resolve_pending(0, keep_new=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["doc_id"], "doc-1")
        self.assertEqual(self.svc.list_pending(), [])

    def test_drop_pending(self):
        self.svc.queue_pending("新知识 B", {}, "complement", "互补")
        r = self.svc.resolve_pending(0, keep_new=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "dropped")
        self.assertEqual(self.svc.list_pending(), [])


class TestMemoryNoteHits(unittest.TestCase):
    def test_throttle(self):
        from services import memory_service as ms
        svc = ms.MemoryService()
        calls = []
        svc.record_access = lambda doc_id, coll=None, layer=None: calls.append(doc_id) or True
        hits = [{"id": "d1", "metadata": {"layer": "core"}},
                {"id": "d2", "metadata": {"layer": "cold"}}]
        ms._ACCESS_TOUCH.clear()
        self.assertEqual(svc.note_hits(hits), 2)
        self.assertEqual(svc.note_hits(hits), 0)   # 一小时节流
        ms._ACCESS_TOUCH.clear()


class TestPromptTemplateCRUD(unittest.TestCase):
    def test_crud(self):
        from services.prompt_engineer import PromptEngineer
        tmp = tempfile.mkdtemp(prefix="tpl_")
        try:
            pe = PromptEngineer()
            pe._dir = Path(tmp)
            pe.save_template("t1", "内容", style="简洁")
            self.assertEqual(pe.get_template("t1")["content"], "内容")
            self.assertEqual(len(pe.list_templates()), 1)
            self.assertTrue(pe.delete_template("t1"))
            self.assertFalse(pe.delete_template("t1"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
