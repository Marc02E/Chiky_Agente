import ast
import operator
from collections.abc import Callable
from typing import Any, cast

from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk

_BINARY_OPS: dict[type[ast.operator], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[..., Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_expression(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left = _eval_expression(node.left)
        right = _eval_expression(node.right)
        return cast("int | float", _BINARY_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return cast(
            "int | float", _UNARY_OPS[type(node.op)](_eval_expression(node.operand))
        )
    raise ValueError("expression uses unsupported syntax")


async def _calculate(arguments: dict[str, Any]) -> dict[str, Any]:
    expression = str(arguments.get("expression", ""))
    try:
        result = _eval_expression(ast.parse(expression, mode="eval").body)
    except (ValueError, ZeroDivisionError, SyntaxError) as exc:
        return {"error": str(exc)}
    return {"result": result}


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def _list_tools(_: dict[str, Any]) -> dict[str, Any]:
        return {"tools": registry.names()}

    registry.register(
        ToolDefinition(
            "calculator",
            ToolRisk.LOW,
            False,
            _calculate,
            argument_schema={"expression": "string"},
        )
    )
    registry.register(
        ToolDefinition(
            "list_tools",
            ToolRisk.LOW,
            False,
            _list_tools,
            argument_schema={},
        )
    )
    return registry