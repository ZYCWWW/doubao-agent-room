"""
想象空间逻辑校验器
三类检查：
1. 未定义符号表（变量/概念引用前未定义）
2. 依赖图找环（推理步骤循环依赖）
3. 矛盾检测 + 矛盾三级分类（logical / representation / bridge）
"""
import re
from core.thought_lib import thought_lib
from core.logger import root_logger


# ---------- 1. 未定义符号 ----------

def check_undefined_symbols(text: str, defined: set = None) -> list:
    """
    检查文本中引用但未定义的符号（大写标识符/公式变量）
    :param defined: 已定义的符号集合（上下文已知）
    :return: [{symbol, line}] 未定义符号列表
    """
    defined = set(defined or ())
    # 匹配可能的符号引用：形如 X、A1、T(A)、F(B) 等独立标识符
    pattern = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9]*)\s*(?=\()")
    pattern2 = re.compile(r"(?<![A-Za-z0-9_])([A-Z])(?![A-Za-z0-9_])")
    found = set()
    for line in text.splitlines():
        for m in pattern.finditer(line):
            sym = m.group(1)
            if sym not in defined:
                found.add((sym, line.strip()[:80]))
        for m in pattern2.finditer(line):
            sym = m.group(1)
            if sym not in defined:
                found.add((sym, line.strip()[:80]))
    out = []
    seen = set()
    for sym, line in sorted(found):
        if sym in seen:
            continue
        seen.add(sym)
        out.append({"symbol": sym, "context": line})
    return out


# ---------- 2. 依赖图找环 ----------

def _extract_edges(steps: list) -> list:
    """从步骤列表提取 (produces, requires) 边；steps 为 dict 列表含 id/produces/requires"""
    edges = []
    for s in steps:
        sid = s.get("id")
        if not sid:
            continue
        for r in s.get("requires", []) or []:
            edges.append((str(r), str(sid)))
    return edges


def find_dependency_cycle(steps: list) -> list:
    """在步骤依赖图中找环，返回环路径（无环返回空列表）"""
    edges = _extract_edges(steps)
    nodes = set()
    for a, b in edges:
        nodes.add(a)
        nodes.add(b)
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    visiting = set()
    done = set()
    stack = []

    def dfs(u):
        visiting.add(u)
        stack.append(u)
        for v in adj.get(u, []):
            if v in visiting:
                # 找到环：从 v 到当前栈尾
                i = stack.index(v)
                return stack[i:] + [v]
            if v not in done:
                r = dfs(v)
                if r:
                    return r
        stack.pop()
        visiting.discard(u)
        done.add(u)
        return []

    for n in sorted(nodes):
        if n not in done:
            r = dfs(n)
            if r:
                return r
    return []


# ---------- 3. 矛盾检测与三级分类 ----------

CONTRADICTION_LEVELS = ("logical_contradiction", "representation_conflict", "bridge_conflict")


def _detect_contradiction_pairs(assertions: list) -> list:
    """
    在断言列表中找矛盾对（朴素实现）：
    - 数值断言冲突：A = x 与 A = y（x≠y）
    - 方向断言冲突：p 为真 与 p 为假
    :param assertions: [{text, source?}]
    :return: [{a, b, kind, detail}]
    """
    out = []
    n = len(assertions)
    for i in range(n):
        for j in range(i + 1, n):
            ta = assertions[i]
            tb = assertions[j]
            kind, detail = _compare_assertions(ta, tb)
            if kind:
                out.append({"a": ta, "b": tb, "kind": kind, "detail": detail})
    return out


def _compare_assertions(a, b):
    """比较两条断言，返回 (矛盾类别 or None, 说明)"""
    a_text = a.get("text", "").strip()
    b_text = b.get("text", "").strip()
    # 标识符：支持中文（变量/概念名）
    ident = r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*"
    # 数值冲突：X = n1 与 X = n2
    m1 = re.search("(" + ident + r")\s*[=＝]\s*(-?\d+(?:\.\d+)?)", a_text)
    m2 = re.search("(" + ident + r")\s*[=＝]\s*(-?\d+(?:\.\d+)?)", b_text)
    if m1 and m2 and m1.group(1) == m2.group(1):
        try:
            v1, v2 = float(m1.group(2)), float(m2.group(2))
            if abs(v1 - v2) > 1e-9:
                return "logical_contradiction", f"{m1.group(1)} = {v1} 与 = {v2}"
        except ValueError:
            pass
    # 方向冲突：X (不)支持/是/允许/可以 … P
    pos_verbs = r"(?:是|为|属于|在|支持|允许|可以|能|会)"
    neg_verbs = r"(?:不是|不为|不属于|不在|不支持|不允许|不可以|不能|不会)"
    pos_a = re.search(ident + r"\s+" + pos_verbs + r"\s+(\S+)", a_text)
    pos_b = re.search(ident + r"\s+" + pos_verbs + r"\s+(\S+)", b_text)
    neg_a = re.search(ident + r"\s+" + neg_verbs + r"\s+(\S+)", a_text)
    neg_b = re.search(ident + r"\s+" + neg_verbs + r"\s+(\S+)", b_text)
    if pos_a and neg_b and pos_a.group(1) == neg_b.group(1):
        return "logical_contradiction", f"{pos_a.group(0)} 与 不是 {neg_b.group(1)}"
    if neg_a and pos_b and neg_a.group(1) == pos_b.group(1):
        return "logical_contradiction", f"{neg_a.group(0)} 与 是 {pos_b.group(1)}"
    return None, ""


def classify_contradiction(kind: str, can_reresent: bool = False,
                           bridge_mismatch: bool = False) -> str:
    """
    对矛盾做三级分类：
    - logical_contradiction：同体系自相矛盾 → 阻断
    - representation_conflict：可换表示消解 → 触发重表示
    - bridge_conflict：桥接锚点冲突 → 修正桥接
    """
    if bridge_mismatch:
        return "bridge_conflict"
    if can_reresent:
        return "representation_conflict"
    return "logical_contradiction"


def validate(session_id: str, nodes: list, defined_symbols: set = None,
             assertions: list = None) -> dict:
    """
    对想象空间节点做整体校验
    :param nodes: [{id, title, type, content}] 节点列表（content 为正文）
    :return: {ok, undefined, cycle, contradictions}
    """
    defined = set(defined_symbols or ())
    result = {"ok": True, "undefined": [], "cycle": [], "contradictions": []}

    # 符号检查（把已有节点标题视为已定义）
    all_text = []
    for nd in nodes:
        title = nd.get("title", "")
        if title:
            defined.add(title.strip())
        all_text.append(nd.get("content", ""))

    text = "\n".join(all_text)
    undef = check_undefined_symbols(text, defined)
    if undef:
        result["undefined"] = undef
        result["ok"] = False
        root_logger.info(f"想象空间校验：未定义符号 {len(undef)} 个")

    # 依赖环
    steps = [{"id": nd.get("id"), "produces": [nd.get("title", "")],
              "requires": nd.get("requires", []) or []} for nd in nodes]
    cycle = find_dependency_cycle(steps)
    if cycle:
        result["cycle"] = cycle
        result["ok"] = False
        root_logger.info(f"想象空间校验：依赖环 {cycle}")

    # 矛盾检测
    if assertions:
        pairs = _detect_contradiction_pairs(assertions)
        for p in pairs:
            p["level"] = classify_contradiction(p["kind"])
            if p["level"] == "logical_contradiction":
                result["ok"] = False
        result["contradictions"] = pairs

    return result


def contradiction_guidance(level: str) -> str:
    """按矛盾级别给出处理指引（与思维架构库联动）"""
    if level == "logical_contradiction":
        arch = thought_lib.get("contradiction_classification")
        return f"逻辑矛盾：阻断推理，回查前提/步骤。参考思维架构：{arch.title if arch else '矛盾三级分类'}"
    if level == "representation_conflict":
        arch = thought_lib.get("multi_world_representation")
        return f"表示冲突：触发重表示。参考思维架构：{arch.title if arch else '多世界重表示推理'}"
    if level == "bridge_conflict":
        return "桥接冲突：修正桥接约束后重试，不轻易丢弃任一世界"
    return "未知矛盾级别"
