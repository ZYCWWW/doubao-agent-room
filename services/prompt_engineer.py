"""
提示词工程助手（Prompt Engineer）
- 模板 CRUD：cache/prompt_templates/<name>.json
- optimize：用 light 模型优化用户提示词，返回 原版/优化版/改进点
- compare：并排对比
- submit：用户确认后，把提示词交给 LLM 执行

流程符合用户预期：先看助手输出，确认是所需后，再提交给 LLM。
"""
import json
import time
import os
from core.config_loader import PROJECT_ROOT
from core.logger import root_logger
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.model_router import router
from core.json_extract import extract_json

TEMPLATE_DIR = PROJECT_ROOT / "cache" / "prompt_templates"

OPTIMIZE_SYSTEM = (
    "你是提示词工程专家。用户会给出一条提示词，请优化它，使其更清晰、具体、"
    "结构完整、对 LLM 友好。只输出 JSON，格式："
    '{"optimized": "优化后的完整提示词", "changes": ["改进点1", "改进点2"], '
    '"note": "一句话说明优化思路"}'
    "要求：保留用户原始意图，不添加用户未要求的内容，语言与用户提示词一致。"
)


class PromptEngineer:
    def __init__(self):
        self._dir = TEMPLATE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---------- 模板 CRUD ----------

    def _path(self, name: str):
        safe = name.strip().replace("/", "_").replace("\\", "_")
        return self._dir / f"{safe}.json"

    def save_template(self, name: str, content: str, style: str = "通用") -> dict:
        """保存/覆盖一个提示词模板"""
        data = {
            "name": name.strip(),
            "style": style,
            "content": content,
            "updated_ts": time.time(),
        }
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        root_logger.info(f"提示词模板已保存: {name}")
        return data

    def get_template(self, name: str) -> dict:
        """读取模板；不存在返回 None"""
        fp = self._path(name)
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_templates(self) -> list:
        """列出全部模板摘要"""
        out = []
        if not self._dir.exists():
            return out
        for fp in sorted(self._dir.glob("*.json")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                out.append({"name": data.get("name", fp.stem),
                            "style": data.get("style", ""),
                            "updated_ts": data.get("updated_ts")})
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def delete_template(self, name: str) -> bool:
        """删除模板；不存在返回 False"""
        fp = self._path(name)
        if not fp.exists():
            return False
        fp.unlink()
        root_logger.info(f"提示词模板已删除: {name}")
        return True

    # ---------- 优化 / 对比 / 提交 ----------

    def optimize(self, prompt: str, style: str = None) -> dict:
        """
        用 light 模型优化提示词
        返回 {original, optimized, changes, note, success}
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return {"success": False, "error": "提示词为空"}
        user_msg = f"请优化下面这条提示词（风格偏好：{style or '通用'}）：\n\n{prompt}"
        try:
            resp = ask(
                [{"role": "system", "content": OPTIMIZE_SYSTEM},
                 {"role": "user", "content": user_msg}],
                temperature=0.3,
                model=router.route("prompt_optimize"),
                use_cache=False)
            if not resp.success:
                return {"success": False, "error": f"LLM 调用失败: {resp.content}"}
            obj = extract_json(resp.content)
            if not isinstance(obj, dict) or not obj.get("optimized"):
                return {"success": False, "error": f"解析优化结果失败: {resp.content[:200]}"}
            return {
                "success": True,
                "original": prompt,
                "optimized": obj["optimized"],
                "changes": obj.get("changes", []),
                "note": obj.get("note", ""),
            }
        except Exception as e:
            return {"success": False, "error": f"优化异常: {e}"}

    def compare(self, original: str, optimized: str, changes: list = None) -> str:
        """生成原版 vs 优化版 的 markdown 对比"""
        return (
            "### 提示词对比\n\n"
            "**原版：**\n```\n" + (original or "").strip() + "\n```\n\n"
            "**优化版：**\n```\n" + (optimized or "").strip() + "\n```\n\n"
            "**改进点：**\n" + ("\n".join(f"- {c}" for c in (changes or [])) or "- 无") + "\n"
        )

    def submit(self, prompt: str, model: str = None, system: str = None) -> dict:
        """把提示词交给 LLM 执行（用户确认后调用）"""
        prompt = (prompt or "").strip()
        if not prompt:
            return {"success": False, "error": "提示词为空"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = ask(messages, temperature=0.3,
                               model=model or router.route("main"))
        return {
            "success": resp.success,
            "response": resp.content,
        }


prompt_engineer = PromptEngineer()
