# calc_plugin/module.py
"""示例插件：安全计算器（演示插件系统懒加载与热加载）"""
import ast
import operator

_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.BitXor: operator.pow,  # 数学习惯：^ 当幂，否则 (1+2)^3 会抛 BitXor 不支持
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    raise ValueError(f"不支持的表达式节点：{type(node).__name__}")


def calc(expr: str, session_id: str = ""):
    """安全计算数学表达式（白名单 AST 求值）"""
    try:
        tree = ast.parse(str(expr), mode="eval")
        result = _eval_node(tree.body)
        return f"{expr} = {result}"
    except Exception as e:
        return f"计算失败：{e}"


plugin_export = {
    "calc": {"desc": "安全数学计算：如 '2+3*4'、'2**10'（^ 也可当幂），返回表达式和结果", "func": calc},
}
