"""
Ограниченный язык сценариев без eval, Python-кода и shell-команд.
"""

import math
import re
import shlex

from pydantic import BaseModel, ConfigDict

from hid_pilot.keyboard import KEYS as KEY_USAGES

KEYS = frozenset(KEY_USAGES)


class Instruction(BaseModel):
    model_config = ConfigDict(frozen=True)
    line: int
    operation: str
    args: tuple[str, ...] = ()
    body: tuple["Instruction", ...] = ()


def script_variables(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line, raw in enumerate(text.splitlines(), 1):
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.escape = ""
        parts = list(lexer)
        if parts and parts[0] == "set":
            if len(parts) != 3 or not re.fullmatch(r"[a-z][a-z0-9_]*", parts[1]):
                raise ValueError(f"Строка {line}: set имя значение")
            if parts[1] in result:
                raise ValueError(f"Строка {line}: переменная уже задана")
            result[parts[1]] = parts[2]
    return result


def number(value: str, low: float, high: float) -> float:
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"Число должно быть в диапазоне {low}–{high}")
    return result


def parse_script(text: str) -> tuple[Instruction, ...]:
    if len(text) > 100_000:
        raise ValueError("Сценарий слишком большой")
    root: list[Instruction] = []
    stack: list[tuple[int, str, tuple[str, ...], list[Instruction]]] = []
    current = root
    variables = script_variables(text)
    from hid_pilot.extensions import extension_commands, extension_symbol
    extensions = extension_commands()
    code_started = False
    for line, raw in enumerate(text.splitlines(), 1):
        try:
            lexer = shlex.shlex(raw, posix=True)
            lexer.whitespace_split = True
            lexer.escape = ""
            parts = list(lexer)
            if not parts:
                continue
            op, *args_list = parts
            if op == "set":
                if code_started:
                    raise ValueError("Переменные задаются только в начале сценария")
                continue
            code_started = True
            args = tuple(variables.get(value[1:], value) if value.startswith("$") else value
                         for value in args_list)
            if any(value.startswith("$") for value in args):
                raise ValueError("Неизвестная переменная")
            if op == "end" and not args:
                if not stack:
                    raise ValueError("Лишний end")
                start, block, block_args, parent = stack.pop()
                parent.append(Instruction(line=start, operation=block, args=block_args,
                                          body=tuple(current)))
                current = parent
                continue
            if op in {"repeat", "if_found"}:
                if op == "repeat":
                    if len(args) != 1 or not 1 <= int(args[0]) <= 1000:
                        raise ValueError("repeat требует число 1–1000")
                elif args:
                    raise ValueError("if_found не принимает аргументы")
                if len(stack) >= 8:
                    raise ValueError("Слишком глубокая вложенность")
                stack.append((line, op, args, current))
                current = []
                continue
            if op in extensions and len(args) == extensions[op].arguments:
                extension_symbol(extensions[op].config).model_validate(variables)
            elif op == "wait" and len(args) == 1:
                number(args[0], 0, 60)
            elif op == "key" and len(args) == 2:
                if args[0] not in KEYS or not 1 <= int(args[1]) <= 1000:
                    raise ValueError("Недопустимая клавиша или длительность")
            elif op == "move" and len(args) == 2:
                for value in args:
                    number(value, 0, 1)
            elif op == "click" and len(args) in (1, 3):
                if args[0] not in {"left", "right"}:
                    raise ValueError("Кнопка: left или right")
                for value in args[1:]:
                    number(value, 0, 1)
            elif op == "find" and len(args) == 3:
                number(args[1], 0.5, 1)
                number(args[2], 0, 60)
            elif op == "color" and len(args) == 4:
                number(args[0], 0, 1)
                number(args[1], 0, 1)
                if len(args[2]) != 6 or not 0 <= int(args[2], 16) <= 0xFFFFFF:
                    raise ValueError("Цвет задаётся как RRGGBB")
                if not 0 <= int(args[3]) <= 255:
                    raise ValueError("Допуск цвета: 0–255")
            else:
                raise ValueError("Неизвестная команда или неверные аргументы")
            current.append(Instruction(line=line, operation=op, args=args))
        except ValueError as exc:
            raise ValueError(f"Строка {line}: {exc}") from exc
    if stack:
        raise ValueError(f"Строка {stack[-1][0]}: отсутствует end")
    if not root:
        raise ValueError("Сценарий пуст")
    return tuple(root)
