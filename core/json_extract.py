# core/json_extract.py
"""
从 LLM 输出中鲁棒提取 JSON：
- 剥离外层/嵌套 markdown 代码块（```json ```）
- 去除 // 与 /* */ 注释（LLM 常见）
- 截断恢复（补全未闭合括号/引号）
- 多候选：优先取最长合法 JSON
"""
import re
import json
from typing import Any, Optional

_CODE_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """剥掉最外层 markdown 代码块标记，保留内部内容"""
    t = text.strip()
    m = _CODE_FENCE.match(t)
    if m:
        return m.group(1).strip()
    # 无代码块或代码块不完整：去掉行首 ``` 残迹
    return re.sub(r"^```[a-zA-Z]*\s*", "", t).strip()


def _strip_comment(text: str) -> str:
    """去除 // 与 /* */ 注释（字符级单遍扫描，正确处理字符串与跨行块注释）"""
    out = []
    i, n = 0, len(text)
    in_str = False
    in_block = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
            else:
                i += 1
            continue
        if in_str:
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            # 行注释：跳到行尾（保留换行符）
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_json_spans(text: str):
    """找出所有可能是 JSON 的片段（从 { 或 [ 开始，到匹配的闭合符）"""
    spans = []
    for i, ch in enumerate(text):
        if ch not in "{[": 
            continue
        open_ch, close_ch = (ch, "}" if ch == "{" else "]")
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    spans.append((i, j + 1))
                    break
    return spans


def _try_fix_truncated(text: str) -> Optional[str]:
    """尝试修复截断的 JSON：补全引号/括号/尾逗号，返回修复后文本或 None"""
    t = text.rstrip().rstrip(",").rstrip()
    # 统计括号补齐（同时跟踪字符串引号状态；未闭合引号在末尾补一个）
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    in_str = False
    esc = False
    for c in t:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "([{":
            stack.append(c)
        elif c in ")]}":
            if stack and stack[-1] == pairs[c]:
                stack.pop()
            else:
                return None  # 括号错配，无法修复
    if in_str:
        t += '"'
    for open_ch in reversed(stack):
        t += "]" if open_ch == "[" else ("}" if open_ch == "{" else ")")
    return t


def extract_json(text: str) -> Optional[Any]:
    """从模型输出提取 JSON。找不到返回 None。"""
    if not text or not text.strip():
        return None
    t = strip_code_fences(text)

    # 直接尝试
    try:
        return json.loads(t)
    except Exception:
        pass

    # 去注释后重试
    t2 = _strip_comment(t)
    try:
        return json.loads(t2)
    except Exception:
        pass

    # 扫描片段，取最长合法 JSON
    best, best_len = None, -1
    for s, e in _find_json_spans(t2):
        cand = t2[s:e]
        try:
            obj = json.loads(cand)
        except Exception:
            fixed = _try_fix_truncated(cand)
            if fixed is None:
                continue
            try:
                obj = json.loads(fixed)
            except Exception:
                continue
        length = e - s
        if length > best_len:
            best, best_len = obj, length

    # 无完整片段：尝试对"首个 { 或 [ 到末尾"做截断修复（LLM 输出被截断）
    if best is None:
        for ch in ("{", "["):
            s = t2.find(ch)
            if s >= 0:
                fixed = _try_fix_truncated(t2[s:])
                if fixed is not None:
                    try:
                        obj = json.loads(fixed)
                        if obj is not None:
                            return obj
                    except Exception:
                        continue
    return best


def extract_json_list(text: str) -> list:
    """提取 JSON 数组；失败返回空列表"""
    obj = extract_json(text)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [obj]
    return []
