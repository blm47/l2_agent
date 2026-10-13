import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from hid_pilot.controller import ActionController
from hid_pilot.desktop import match_image
from hid_pilot.extensions import extension_commands, extension_symbol
from hid_pilot.script import Instruction

logger = logging.getLogger("hid_pilot.runner")


class Runner:
    def __init__(self, controller: ActionController, base: Path,
                 notify: Callable[[str], None], variables: dict[str, str] | None = None) -> None:
        self.controller = controller
        self.controller.humanize = True
        self.base = base
        self.notify = notify
        self.found: tuple[float, float] | None = None
        self.found_at = 0.0
        self.steps = 0
        self.deadline = time.monotonic() + 36000
        self.variables = variables or {}
        self.extensions = extension_commands()

    def run(self, instructions: tuple[Instruction, ...]) -> None:
        for item in instructions:
            self.controller.checkpoint()
            self.steps += 1
            if self.steps > 100_000 or time.monotonic() > self.deadline:
                raise RuntimeError("Достигнут лимит сценария: 100000 шагов / 10 часов")
            self.notify(f"Строка {item.line}: {item.operation}")
            logger.info("Строка %s: %s", item.line, item.operation)
            args = item.args
            if item.operation in self.extensions:
                command = self.extensions[item.operation]
                config = extension_symbol(command.config).model_validate(self.variables)
                extension_symbol(command.run)(self.controller, self.base, args, config,
                                              self.notify, self.deadline)
            elif item.operation == "repeat":
                for _ in range(int(args[0])):
                    self.controller.checkpoint()
                    self.steps += 1
                    if self.steps > 100_000 or time.monotonic() > self.deadline:
                        raise RuntimeError("Достигнут лимит сценария")
                    self.run(item.body)
            elif item.operation == "if_found":
                if self.found is not None and time.monotonic() - self.found_at <= 1:
                    self.run(item.body)
            elif item.operation == "wait":
                seconds = float(args[0])
                self.controller.wait(seconds + random.uniform(0, min(.1, seconds * .1)))
            elif item.operation == "key":
                self.controller.key(args[0], int(args[1]))
                self.found = None
            elif item.operation == "move":
                self.controller.move(float(args[0]), float(args[1]))
            elif item.operation == "click":
                if len(args) == 3:
                    point = float(args[1]), float(args[2])
                elif self.found is not None and time.monotonic() - self.found_at <= 1:
                    point = self.found
                else:
                    raise RuntimeError("Для click без координат нужен свежий find/color")
                self.controller.click(args[0], *point)
                self.found = None
            elif item.operation == "find":
                self._find(args)
            elif item.operation == "color":
                self.found = None
                x, y = float(args[0]), float(args[1])
                image = self.controller.capture()
                pixel = image[round(y * (image.shape[0] - 1)),
                              round(x * (image.shape[1] - 1))].astype(int)
                expected = np.array([int(args[2][i:i + 2], 16) for i in (4, 2, 0)])
                if np.max(np.abs(pixel - expected)) <= int(args[3]):
                    self.found = x, y
                    self.found_at = time.monotonic()

    def _find(self, args: tuple[str, ...]) -> None:
        self.found = None
        path = self.base / args[0]
        if path.stat().st_size > 10_000_000:
            raise ValueError("Шаблон слишком большой")
        template = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if template is None or template.size > 12_000_000:
            raise ValueError("Шаблон не прочитан или слишком большой")
        deadline = time.monotonic() + float(args[2])
        while True:
            self.controller.checkpoint()
            image = self.controller.capture()
            self.controller.checkpoint()
            self.found = match_image(image, template, float(args[1]))
            if self.found is not None:
                self.found_at = time.monotonic()
                return
            if time.monotonic() >= deadline:
                return
            self.controller.wait(0.1)
