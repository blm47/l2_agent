"""
Автоматический ограниченный архив ошибок наблюдения с фоновой записью.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
import threading
import time

import numpy as np

from hid_pilot.bars import BarProfile
from hid_pilot.debug_capture import encode_frame
from hid_pilot.party import PartyProfile

logger = logging.getLogger("hid_pilot.observation_debug")


class ObservationDebugRecorder:
    def __init__(self, directory: Path, profile: BarProfile) -> None:
        self.directory = directory
        self.profile = profile
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.pending: tuple | None = None
        self.previous: tuple | None = None
        self.last_alive = float("-inf")
        self.last_saved: dict[str, float] = {}
        self.index = 0
        self.worker = threading.Thread(target=self._run, name="ObservationDebugWriter", daemon=True)
        self.worker.start()
        logger.info("Автосохранение ошибок наблюдения: %s; до 32 событий / 256 MiB", directory)

    def observe(self, image: np.ndarray, state: dict) -> None:
        now = time.monotonic()
        target = state.get("target") or {}
        hp = target.get("hp")
        reasons = []
        if state.get("hp") is None or state.get("mp") is None:
            reasons.append("resources_unknown")
        if hp is not None and hp > 0:
            self.last_alive = now
            if self.profile.target_level is not None and target.get("level") is None:
                reasons.append("target_level_unknown")
            if (self.profile.target_panel or self.profile.target_player_panel) and not target.get("name"):
                reasons.append("target_name_unknown")
        elif now - self.last_alive <= 5:
            reasons.append("target_zero" if hp == 0 else "target_hp_lost")
        if self.profile.own_level is not None and state.get("own_level") is None:
            reasons.append("own_level_unknown")
        if target.get("death_marker"):
            reasons.append("death_marker")
        if state.get("party_profile"):
            members = state.get("party", [])
            if not members:
                reasons.append("party_missing")
            elif any(member.get("hp") is None or member.get("mp") is None for member in members):
                reasons.append("party_resources_unknown")
            if any(not member.get("name") for member in members):
                reasons.append("party_name_unknown")
            if (state["party_profile"].get("class_icon")
                    and any(not member.get("player_class") for member in members)):
                reasons.append("party_class_unknown")
        due = [reason for reason in reasons if now - self.last_saved.get(reason, float("-inf"))
               >= (.25 if reason in {"target_hp_lost", "target_zero", "death_marker"} else 3)]
        current = (image, {**state, "captured_at": datetime.now().isoformat(timespec="milliseconds")})
        if due:
            # Только один ожидающий комплект: медленный диск не создаёт очередь кадров в RAM.
            with self.lock:
                self.pending = (self.previous, current, due)
            for reason in due:
                self.last_saved[reason] = now
            self.ready.set()
        self.previous = current

    def write_event(self, previous: tuple | None, current: tuple, reasons: list[str]) -> None:
        files = {}
        for prefix, sample in (("before", previous), ("failure", current)):
            if sample is not None:
                party = sample[1].get("party_profile")
                party_profile = PartyProfile.model_validate(party) if party else None
                for name, data in encode_frame(sample[0], self.profile, party_profile).items():
                    files[f"{prefix}_{name}"] = data
        metadata = {"reasons": reasons, "before": previous[1] if previous else None,
                    "failure": current[1], "profile": self.profile.model_dump(mode="json"),
                    "files": list(files)}
        files["event.json"] = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        if sum(map(len, files.values())) > 8 * 1024**2:
            logger.warning("Комплект диагностики превышает 8 MiB: запись пропущена")
            return
        slot = self.directory / f"event-{self.index % 32:02d}"
        slot.mkdir(parents=True, exist_ok=True)
        # Удаляем только файлы нашего фиксированного слота, без рекурсивного удаления.
        for path in slot.iterdir():
            if path.is_file() and path.suffix in {".png", ".jpg", ".json"}:
                path.unlink()
        for name, data in files.items():
            (slot / name).write_bytes(data)
        self.index += 1
        logger.info("Ошибка наблюдения %s: кадры и ROI сохранены в %s", ", ".join(reasons), slot)

    def _run(self) -> None:
        while True:
            self.ready.wait(.5)
            self.ready.clear()
            with self.lock:
                pending, self.pending = self.pending, None
            if pending is not None:
                try:
                    self.write_event(*pending)
                except Exception:
                    logger.exception("Не удалось сохранить кадры ошибки наблюдения")
            if self.stopped.is_set():
                return

    def close(self) -> None:
        self.stopped.set()
        self.ready.set()
        self.worker.join(timeout=2)
