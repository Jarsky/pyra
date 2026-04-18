"""
Calc plugin — safe math expression evaluator.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Commands:
  !calc <expr>    Evaluate a math expression (e.g. !calc 2+2, sin(pi/4))
  !c <expr>       Alias for !calc
"""

from __future__ import annotations

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command("calc", aliases=["c"], help="Evaluate a math expression", usage="!calc <expr>")
async def cmd_calc(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !calc <expression>  (e.g. !calc 2+2, sin(pi/4))")  # type: ignore[attr-defined]
        return

    expr = " ".join(trigger.args)
    result = _evaluate(expr)
    await bot.say(trigger.target, f"{expr} = {result}")  # type: ignore[attr-defined]


def _evaluate(expr: str) -> str:
    try:
        import math

        from simpleeval import SimpleEval

        evaluator = SimpleEval()
        evaluator.names.update(
            {
                "pi": math.pi,
                "e": math.e,
                "tau": math.tau,
                "inf": math.inf,
                "nan": math.nan,
            }
        )
        evaluator.functions.update(
            {
                "sin": math.sin,
                "cos": math.cos,
                "tan": math.tan,
                "asin": math.asin,
                "acos": math.acos,
                "atan": math.atan,
                "atan2": math.atan2,
                "sqrt": math.sqrt,
                "log": math.log,
                "log2": math.log2,
                "log10": math.log10,
                "exp": math.exp,
                "ceil": math.ceil,
                "floor": math.floor,
                "round": round,
                "abs": abs,
                "pow": pow,
                "factorial": math.factorial,
                "gcd": math.gcd,
                "degrees": math.degrees,
                "radians": math.radians,
            }
        )
        result = evaluator.eval(expr)
        if isinstance(result, float):
            if result != result:  # NaN
                return "nan"
            if abs(result) == float("inf"):
                return "inf"
            # Format nicely
            if result == int(result) and abs(result) < 1e15:
                return str(int(result))
            return f"{result:.10g}"
        return str(result)
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as exc:
        return f"Error: {exc}"
