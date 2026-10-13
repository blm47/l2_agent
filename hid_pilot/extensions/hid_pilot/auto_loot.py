"""
Неблокирующая серия лута: один короткий ввод за шаг наблюдения.
"""

import random


class AutoLoot:
    def __init__(self, now: float) -> None:
        self.remaining = 0
        self.next_at = now + random.uniform(2, 6)

    def due(self, now: float) -> bool:
        return now >= self.next_at

    def committed(self, now: float) -> None:
        if self.remaining == 0:
            self.remaining = random.randint(1, 3)
        self.remaining -= 1
        self.next_at = now + (random.uniform(.45, .55) if self.remaining else random.uniform(2, 6))

    def interrupt(self, now: float) -> None:
        if self.remaining:
            self.remaining = 0
            self.next_at = now + random.uniform(2, 6)
