"""
Плавная случайная траектория с точной конечной точкой внутри окна.
"""

import math
import random


def mouse_path(start: tuple[int, int], end: tuple[int, int], rect: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    left, top, right, bottom = rect
    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance < 3:
        return [end]
    bend = random.uniform(-min(80, distance * .08), min(80, distance * .08))
    control = ((start[0] + end[0]) / 2 - dy / distance * bend,
               (start[1] + end[1]) / 2 + dx / distance * bend)
    points = []
    steps = max(12, math.ceil(distance / 6))
    for index in range(1, steps + 1):
        progress = index / steps
        # Единый разгон и торможение для всей дуги, без остановок в промежуточных точках.
        t = progress * progress * (3 - 2 * progress)
        x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t ** 2 * end[0]
        y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t ** 2 * end[1]
        points.append((max(left, min(right - 1, round(x))), max(top, min(bottom - 1, round(y)))))
    points[-1] = end
    return points
