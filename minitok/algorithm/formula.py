"""A tiny, safe expression language for writing your own ranking formula.

Example formulas::

    3*interest + like_rate + 0.5*freshness
    interest * 2 + (1 if has("#cats") else 0) - 0.3 * popularity
    log10(views + 1) / 7 + random * 0.2
    0 if contains("giveaway") else interest + share_rate

Only arithmetic, comparisons, ``and``/``or``/``not``, ``x if cond else y`` and
the whitelisted functions below are allowed - no attribute access, no
imports, no builtins - so formulas pasted from somewhere else cannot run code.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable

from ..models import Video
from .features import FEATURES

RAW_VARIABLES: dict[str, str] = {
    "views": "Raw view count",
    "likes": "Raw like count",
    "comments": "Raw comment count",
    "shares": "Raw share count",
    "duration": "Length in seconds",
    "age_hours": "Hours since upload (0 if unknown)",
    "author": "Creator name (text)",
    "description": "Caption text",
}

FUNCTIONS: dict[str, Callable[..., Any]] = {
    "log": lambda x: math.log(max(x, 1e-9)),
    "log10": lambda x: math.log10(max(x, 1e-9)),
    "sqrt": lambda x: math.sqrt(max(x, 0)),
    "exp": lambda x: math.exp(min(x, 50)),
    "tanh": math.tanh,
    "abs": abs,
    "min": min,
    "max": max,
    "clamp": lambda x, lo=0.0, hi=1.0: max(lo, min(hi, x)),
}
# has()/contains() need the video, so they are bound per evaluation
CONTEXT_FUNCTIONS = {
    "has": 'has("#tag") / has("@creator") / has("♪sound") - 1 if the video carries that token',
    "contains": 'contains("word") - 1 if the caption contains the text',
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda a, b: a / b if b else 0.0,
    ast.FloorDiv: lambda a, b: a // b if b else 0.0,
    ast.Mod: lambda a, b: a % b if b else 0.0,
    ast.Pow: lambda a, b: a ** min(b, 10),
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class FormulaError(ValueError):
    pass


def allowed_names() -> set[str]:
    return set(FEATURES) | set(RAW_VARIABLES)


class Formula:
    def __init__(self, source: str):
        self.source = source.strip()
        if not self.source:
            raise FormulaError("The formula is empty")
        if len(self.source) > 2000:
            raise FormulaError("The formula is too long (2000 characters max)")
        try:
            tree = ast.parse(self.source, mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"Syntax error: {exc.msg} (column {exc.offset})") from None
        self._check(tree.body)
        self._tree = tree.body

    # -- validation -------------------------------------------------------
    def _check(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool)):
                raise FormulaError(f"Unsupported constant: {node.value!r}")
        elif isinstance(node, ast.Name):
            if node.id not in allowed_names() and node.id not in ("True", "False"):
                raise FormulaError(
                    f"Unknown variable '{node.id}'. Available: {', '.join(sorted(allowed_names()))}"
                )
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BIN_OPS:
                raise FormulaError("Unsupported operator")
            self._check(node.left)
            self._check(node.right)
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY_OPS:
                raise FormulaError("Unsupported operator")
            self._check(node.operand)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                self._check(v)
        elif isinstance(node, ast.Compare):
            if any(type(op) not in _CMP_OPS for op in node.ops):
                raise FormulaError("Unsupported comparison")
            self._check(node.left)
            for c in node.comparators:
                self._check(c)
        elif isinstance(node, ast.IfExp):
            self._check(node.test)
            self._check(node.body)
            self._check(node.orelse)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaError("Only plain function calls are allowed")
            name = node.func.id
            if name not in FUNCTIONS and name not in CONTEXT_FUNCTIONS:
                raise FormulaError(
                    f"Unknown function '{name}'. Available: "
                    f"{', '.join(sorted([*FUNCTIONS, *CONTEXT_FUNCTIONS]))}"
                )
            if node.keywords:
                raise FormulaError("Keyword arguments are not supported")
            for a in node.args:
                self._check(a)
        else:
            raise FormulaError(f"'{ast.unparse(node)}' is not allowed in a formula")

    # -- evaluation -------------------------------------------------------
    def evaluate(self, features: dict[str, float], video: Video) -> float:
        tokens = set(video.tokens())
        desc = video.description.lower()
        env: dict[str, Any] = {
            **features,
            "views": video.views,
            "likes": video.likes,
            "comments": video.comment_count,
            "shares": video.shares,
            "duration": video.duration,
            "age_hours": video.age_hours or 0.0,
            "author": video.author,
            "description": video.description,
        }
        funcs: dict[str, Callable[..., Any]] = {
            **FUNCTIONS,
            "has": lambda tok: 1.0 if str(tok).lower() in tokens else 0.0,
            "contains": lambda text: 1.0 if str(text).lower() in desc else 0.0,
        }
        try:
            value = self._eval(self._tree, env, funcs)
            return float(value)
        except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            raise FormulaError(f"Could not evaluate the formula: {exc}") from None

    def _eval(self, node: ast.AST, env: dict[str, Any], funcs: dict[str, Callable[..., Any]]) -> Any:
        ev = lambda n: self._eval(n, env, funcs)  # noqa: E731
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "True":
                return True
            if node.id == "False":
                return False
            return env[node.id]
        if isinstance(node, ast.BinOp):
            return _BIN_OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for v in node.values:
                    result = ev(v)
                    if not result:
                        return result
                return result
            result = False
            for v in node.values:
                result = ev(v)
                if result:
                    return result
            return result
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                if not _CMP_OPS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return ev(node.body) if ev(node.test) else ev(node.orelse)
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            return funcs[node.func.id](*[ev(a) for a in node.args])
        raise FormulaError("Unsupported expression")  # pragma: no cover - _check guards this


def help_text() -> str:
    lines = ["Variables (0..1 features):"]
    lines += [f"  {k:<13} {v}" for k, v in FEATURES.items()]
    lines.append("Raw values:")
    lines += [f"  {k:<13} {v}" for k, v in RAW_VARIABLES.items()]
    lines.append("Functions: " + ", ".join(sorted(FUNCTIONS)))
    lines += [f"  {v}" for v in CONTEXT_FUNCTIONS.values()]
    lines.append("Operators: + - * / ** %, < > == !=, and or not, x if cond else y")
    return "\n".join(lines)
