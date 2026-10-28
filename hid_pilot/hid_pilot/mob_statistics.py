"""
Счётчик наблюдаемых смертей монстров по непрерывной последовательности таргета.
"""

import logging
import sqlite3
from collections import Counter
from pathlib import Path

from hid_pilot.target import TargetObservation

logger = logging.getLogger("hid_pilot.mob_statistics")


class MobStatistics:
    def __init__(self, started: float) -> None:
        self.started = started
        self.next_report = started + 60
        self.counts: Counter[str] = Counter()
        self.minute_count = 0
        self.unconfirmed_kills = 0
        self.previous: TargetObservation | None = None
        self.armed = False
        self.dead_frames = 0
        self.live_frames = 0
        self.confirmed_dead = False
        self.database: Path | None = None

    def remember(self, target: TargetObservation) -> None:
        if self.database is None or target.kind != "monster" or not target.name or target.confidence < .9:
            return
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS monsters (name TEXT PRIMARY KEY, level INTEGER, last_seen TEXT)")
            if (connection.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] >= 256
                    and connection.execute("SELECT 1 FROM monsters WHERE name=?", (target.name,)).fetchone() is None):
                return
            connection.execute("INSERT INTO monsters VALUES (?, ?, datetime('now')) "
                               "ON CONFLICT(name) DO UPDATE SET level=COALESCE(excluded.level, monsters.level), "
                               "last_seen=excluded.last_seen", (target.name, target.level))

    def known_names(self) -> dict[str, int | None]:
        if self.database is None or not self.database.exists():
            return {}
        with sqlite3.connect(self.database) as connection:
            return dict(connection.execute("SELECT name, level FROM monsters ORDER BY last_seen DESC LIMIT 256"))

    def summary(self, now: float) -> str:
        elapsed = max(0, int(now - self.started))
        names = self.ranked_counts()
        return (f"ФАРМ — {elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}; "
                f"подтверждено убийств: {sum(self.counts.values())}; "
                f"не подтверждено убийств: {self.unconfirmed_kills}\n{names}")

    def record_unconfirmed_kill(self) -> None:
        """
        Учитывает завершение боя без подтверждения смерти отдельно от убийств.
        """
        self.unconfirmed_kills += 1

    def ranked_counts(self) -> str:
        """
        Выводит все виды мобов по убыванию убийств, при равенстве — по имени.
        """
        ranked = sorted(self.counts.items(), key=lambda item: (-item[1], item[0].casefold(), item[0]))
        return "\n".join(f"{rank}. {count} — {name}" for rank, (name, count) in enumerate(ranked, 1)) or "нет"

    def record_kill(self, name: str | None) -> None:
        """
        Учитывает подтверждённый боевым циклом переход живой цели в два нулевых кадра.
        """
        name = name or "цель без имени"
        name = name if name in self.counts or len(self.counts) < 256 else "прочие"
        self.counts[name] += 1
        self.minute_count += 1

    def reset_target(self) -> None:
        self.previous = None
        self.armed = False
        self.dead_frames = 0
        self.live_frames = 0
        self.confirmed_dead = False

    def observe(self, target: TargetObservation, now: float) -> None:
        if not 0 <= now - target.at <= 1:
            self.reset_target()
            return
        if self.previous is not None and target.at <= self.previous.at:
            return
        if (target.kind != "monster" or not target.name or target.confidence < .9
                or target.dead is None):
            self.reset_target()
            return
        same = (self.previous is not None and self.previous.kind == "monster"
                and self.previous.name == target.name and self.previous.epoch == target.epoch
                and target.at - self.previous.at <= 2)
        if not same:
            self.armed = False
            self.dead_frames = 0
            self.live_frames = 0
            self.confirmed_dead = False
        if target.dead is False and target.hp is not None and target.hp > 0:
            self.live_frames += 1
            self.armed = self.live_frames >= (2 if self.confirmed_dead else 1)
            self.dead_frames = 0
        elif target.dead:
            self.live_frames = 0
            if self.armed:
                self.dead_frames += 1
                if self.dead_frames >= 2:
                    self.record_kill(target.name)
                    self.armed = False
                    self.confirmed_dead = True
            else:
                self.confirmed_dead = True
        self.previous = target

    def report(self, now: float) -> str | None:
        if now < self.next_report:
            return None
        elapsed = max(0, int(now - self.started))
        names = self.ranked_counts()
        message = (f"Мобы по таргету: всего {sum(self.counts.values())}, за последний интервал {self.minute_count}; "
                   f"не подтверждено убийств: {self.unconfirmed_kills}; "
                   f"время {elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}\n{names}")
        logger.info(message)
        self.minute_count = 0
        self.next_report = now + 60
        return message
