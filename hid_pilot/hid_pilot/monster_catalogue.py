"""
Общая локальная база имён монстров и подтверждение независимых результатов OCR.
"""

import logging
import sqlite3
from pathlib import Path

from hid_pilot.target import TargetObservation

logger = logging.getLogger("hid_pilot.monster_catalogue")


def normalize_name(name: str) -> str:
    return " ".join(name.split())


class MonsterCatalogue:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=.1) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS monsters "
                               "(name TEXT PRIMARY KEY, level INTEGER, last_seen TEXT)")
        self.names = self.load()

    def load(self) -> dict[str, tuple[str, int | None]]:
        with sqlite3.connect(self.path, timeout=.1) as connection:
            rows = connection.execute("SELECT name, level FROM monsters LIMIT 4096")
            return {normalize_name(name).casefold(): (name, level) for name, level in rows}

    def lookup(self, name: str) -> str | None:
        known = self.names.get(normalize_name(name).casefold())
        return known[0] if known is not None else None

    def confirm(self, name: str, level: int | None) -> str | None:
        name = normalize_name(name)
        key = name.casefold()
        canonical = self.lookup(name)
        if canonical is None and len(self.names) >= 4096:
            return None
        canonical = canonical or name
        with sqlite3.connect(self.path, timeout=.1) as connection:
            connection.execute("INSERT INTO monsters VALUES (?, ?, datetime('now')) "
                               "ON CONFLICT(name) DO UPDATE SET level=COALESCE(excluded.level, monsters.level), "
                               "last_seen=excluded.last_seen", (canonical, level))
        old_level = self.names.get(key, (canonical, None))[1]
        self.names[key] = canonical, level if level is not None else old_level
        logger.info("Имя монстра подтверждено OCR и сохранено в общую базу: %s, уровень %s", canonical, level)
        return canonical


class MonsterNameConsensus:
    def __init__(self, catalogue: MonsterCatalogue) -> None:
        self.catalogue = catalogue
        self.pending: str | None = None
        self.confirmations = 0
        self.last_at = float("-inf")
        self.last_valid_at = float("-inf")
        self.saved = False

    def observe(self, observation: TargetObservation, now: float) -> TargetObservation:
        if observation.at <= self.last_at:
            return observation.model_copy(update={"name": None, "confidence": 0})
        self.last_at = observation.at
        name = normalize_name(observation.name or "")
        valid = (observation.kind == "monster" and observation.confidence >= .9
                 and 0 <= now - observation.at <= 2 and 2 <= len(name) <= 80
                 and any(char.isalpha() for char in name)
                 and all(char.isprintable() for char in name))
        if not valid:
            self.pending = None
            self.confirmations = 0
            self.saved = False
            return observation.model_copy(update={"name": None if observation.kind == "monster" else observation.name})
        if name.casefold() != (self.pending or "").casefold() or observation.at - self.last_valid_at > 3:
            self.pending = name
            self.confirmations = 0
            self.saved = False
        self.last_valid_at = observation.at
        self.confirmations = min(6, self.confirmations + 1)
        # Сначала два одинаковых чтения, затем сверка; новые имена требуют шести.
        canonical = self.catalogue.lookup(name) if self.confirmations >= 2 else None
        if self.confirmations >= 6 and not self.saved:
            canonical = self.catalogue.confirm(name, observation.level)
            self.saved = canonical is not None
        return observation.model_copy(update={"name": canonical, "raw_name": name,
                                               "name_confirmations": self.confirmations,
                                               "confidence": observation.confidence if canonical else 0})
