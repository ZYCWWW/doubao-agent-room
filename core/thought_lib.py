"""
思维架构库（Thought Vault）
================
存放"如何思考"的元知识：推理框架、方法论、分析模式。
- 每个条目 = thought/<name>.md，带 frontmatter（name/title/tags/desc）
- 供 Agent 推理、想象空间、研究流水线加载为思维提示
- 用户可自由增删改条目文件，无需改代码

用法：
    from core.thought_lib import thought_lib
    thought_lib.list()                 # [{name,title,tags,desc}]
    arch = thought_lib.get("scientific_pipeline")
    thought_lib.render_prompt(["scientific_pipeline", "react_loop"])
"""
from pathlib import Path
from core.config_loader import PROJECT_ROOT

THOUGHT_ROOT = PROJECT_ROOT / "thought"


def _parse_frontmatter(text: str):
    """解析简易 frontmatter：--- 开头，key: value 行，支持 [a, b] 列表"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        meta[k] = v
    body = parts[2].lstrip("\n")
    return meta, body


class ThoughtArch:
    """一个思维架构条目"""

    def __init__(self, name: str, title: str, tags: list, desc: str, body: str, path: Path,
                 order: int = None):
        self.name = name
        self.title = title
        self.tags = tags
        self.desc = desc
        self.body = body
        self.path = path
        self.order = order

    def to_prompt(self, max_len: int = 2000) -> str:
        """渲染为可注入 system prompt 的片段（超长截断）"""
        body = self.body.strip()
        if max_len > 0 and len(body) > max_len:
            body = body[:max_len] + "\n...(截断)"
        return f"【思维架构·{self.title}】\n{body}"

    def steps(self, max_items: int = 6, max_len: int = 80) -> str:
        """抽取正文里的编号步骤，返回紧凑的步骤骨架。

        注入提示词时用它代替全文：模型更容易照着一个短步骤表走，
        也比塞 600 字散文省 token。
        md 里常见"3. **双符号系统**："这种只有标题、内容在下一行，
        所以编号行之后会续接正文，直到补够长度。
        """
        import re

        items = []  # [[序号, 文本]]
        sealed = False  # 遇到章节标题后，不再把后续正文续到上一步
        for line in self.body.splitlines():
            m = re.match(r"^\s*(\d+)[.、)]\s*(.+)$", line)
            if m:
                if len(items) >= max_items:
                    break
                items.append([m.group(1), re.sub(r"\*\*|`", "", m.group(2)).strip()])
                sealed = False
                continue
            if line.lstrip().startswith("#"):
                sealed = True
                continue
            if sealed or not items or len(items[-1][1]) >= max_len // 2:
                continue
            tail = re.sub(r"\*\*|`", "", line).strip()
            if not tail:
                continue
            joined = (items[-1][1] + " " + tail).strip()
            items[-1][1] = joined[:max_len] + ("…" if len(joined) > max_len else "")

        if not items:
            head = self.body.strip().replace("\n", " ")[:max_len]
            return f"（无编号步骤，按正文开头执行：{head}…）"
        return "\n".join(f"{n}. {t}" for n, t in items)


    def core(self, max_len: int = 140) -> str:
        """取正文开头几行（典型问题 + 核心思想），注入时放在步骤前面。

        steps() 只留编号行，会把"这方法在解决什么问题""为什么这么想"全部丢掉，
        模型看到的就只剩一张待填的表——只会套模板，不会判断。这里补回那一段。
        """
        out, total = [], 0
        import re

        for raw in self.body.splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if re.match(r"^\s*\d+[.、)]", s):  # 编号步骤归 steps()，这里只要前面那段
                break
            s = s.lstrip("> ").replace("**", "").replace("`", "")
            out.append(s)
            total += len(s)
            if total >= max_len or len(out) >= 3:
                break
        text = " ".join(out).strip()
        return text[:max_len] + ("…" if len(text) > max_len else "")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "tags": self.tags,
            "desc": self.desc,
            "order": self.order,
        }


class ThoughtLib:
    def __init__(self, root: Path = None):
        self._root = Path(root) if root else THOUGHT_ROOT
        self._cache: dict[str, ThoughtArch] = {}

    def _load_all(self):
        if self._cache:
            return
        if not self._root.exists():
            return
        for fp in sorted(self._root.glob("*.md")):
            if fp.name == "index.md":
                continue
            try:
                text = fp.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(text)
                name = meta.get("name") or fp.stem
                if not body.strip():
                    continue
                self._cache[name] = ThoughtArch(
                    name=name,
                    title=meta.get("title", name),
                    tags=meta.get("tags", []),
                    desc=meta.get("desc", ""),
                    body=body,
                    path=fp,
                    order=int(meta["order"]) if str(meta.get("order", "")).strip().isdigit() else None,
                )
            except OSError:
                continue

    def list(self) -> list:
        """全部条目摘要（按 order 升序，无 order 的按名称排在后面）"""
        self._load_all()
        archs = list(self._cache.values())
        archs.sort(key=lambda a: (a.order if a.order is not None else 10_000, a.name))
        return [a.to_dict() for a in archs]

    def get(self, name: str):
        """按 name 取条目；不存在返回 None"""
        self._load_all()
        return self._cache.get(name)

    def recommend(self, query: str, max_items: int = 2, min_score: int = 3) -> list:
        """按任务文本推荐方法论（本地关键词打分，无 LLM）。
        返回条目 name 列表（按匹配分降序，最多 max_items 条）。

        min_score：命中 bigram 数下限。实测真命中 50+ 分（题目词汇与正文重叠多），
        误命中只有 1–2 分（个别常用词偶合），故默认 3，把噪声挡在门外。
        """
        self._load_all()
        if not query or not query.strip():
            return []
        import re

        def _cn_grams(s: str) -> set:
            out = set()
            for seg in re.findall(r"[\u4e00-\u9fa5]+", s):
                for n in (2, 3, 4):
                    out.update(seg[i:i + n] for i in range(len(seg) - n + 1))
            return out

        q_grams = _cn_grams(query.lower()) | {t for t in re.findall(r"[a-z0-9]+", query.lower())}
        if not q_grams:
            return []
        scored = []
        for a in self._cache.values():
            # 标题+标签+描述滑窗匹配（权重高），正文整段包含（权重低）
            key = _cn_grams(f"{a.title} {' '.join(a.tags)} {a.desc}".lower())
            score = sum(1 for t in q_grams if t in key)
            score += sum(1 for t in q_grams if t in a.body[:600].lower())
            if score >= min_score:
                scored.append((score, a.order if a.order is not None else 10_000, a.name))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [n for _, _, n in scored[:max_items]]

    def catalog(self, max_desc: int = 48) -> str:
        """全部条目的一行目录（name：一句话），用于"未命中时先让模型自选方法"。

        首行固定是 none：方法论是 ReAct 的可选辅助，选 none 就等于只用 ReAct。
        """
        self._load_all()
        archs = sorted(self._cache.values(),
                       key=lambda a: (a.order if a.order is not None else 10_000, a.name))
        lines = ["- none：不使用任何方法，直接按 ReAct 的 Thought→Action→Observation 推进"]
        for a in archs:
            d = (a.desc or "").strip().replace("\n", " ")
            if len(d) > max_desc:
                d = d[:max_desc] + "…"
            lines.append(f"- {a.name}：{d}" if d else f"- {a.name}")
        return "\n".join(lines)

    def render_prompt(self, names=None, max_len: int = 2000) -> str:
        """渲染指定条目为 prompt 片段；names=None 时按 order 渲染全部"""
        self._load_all()
        if names is None:
            archs = list(self._cache.values())
            archs.sort(key=lambda a: (a.order if a.order is not None else 10_000, a.name))
        else:
            archs = [self._cache[n] for n in names if n in self._cache]
        if not archs:
            return ""
        return "\n\n".join(a.to_prompt(max_len=max_len) for a in archs)

    def reload(self):
        """清缓存重新扫描（新增条目后调用）"""
        self._cache = {}


thought_lib = ThoughtLib()
