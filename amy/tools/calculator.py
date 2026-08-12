"""
tools/calculator.py — Safe math evaluator. No eval(), uses ast.
"""
import ast
import math
import logging
import re

logger = logging.getLogger("amy.tools.calculator")

_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "pow": pow, "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e,
}

_ALLOWED_NODE_TYPES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.Name, ast.Load,
)


def _safe_eval(expr: str) -> float | int:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise ValueError(f"Unsafe expression: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _SAFE_NAMES:
            raise ValueError(f"Unknown name: {node.id}")
    return eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, _SAFE_NAMES)


def _normalize(expr: str) -> str:
    """Normalize common shortcuts users type."""
    expr = expr.strip()
    expr = re.sub(r'[,،]', '', expr)       # remove thousand separators
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r'(\d)\s*\(', r'\1*(', expr)  # 2(3) → 2*(3)
    return expr


def calculate(expression: str) -> str:
    """
    Safely evaluates a math expression and returns formatted result.
    """
    if not expression or not expression.strip():
        return "No expression provided."

    clean = _normalize(expression)
    logger.info("Calculating: '%s' (raw: '%s')", clean, expression)

    try:
        result = _safe_eval(clean)
        # Format nicely: no .0 for integers
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        formatted = f"{expression} = {result:,}" if "," not in expression else f"{expression} = {result}"
        return formatted
    except ZeroDivisionError:
        return "Division by zero."
    except ValueError as e:
        return f"Invalid expression: {e}"
    except Exception as e:
        logger.error("Calculator error: %s", e)
        return "Couldn't calculate that."
