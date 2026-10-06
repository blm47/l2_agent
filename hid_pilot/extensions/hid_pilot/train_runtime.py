"""
Расширение healer_train: проверки фаз до клика и скилла, без движения при потере данных.
"""

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from hid_pilot.bars import BarProfile
from hid_pilot.controller import ActionController
from hid_pilot.healer import HealDecision, HealerSnapshot
from hid_pilot.healer_runtime import Healer, TargetChanged
from hid_pilot.paralysis import ParalysisDetector, ParalysisProfile
from hid_pilot.party import PartyProfile
from hid_pilot.train_healer import TrainConfig, TrainPolicy, TrainSnapshot
from hid_pilot.train_observer import TrainObserver, TrainReader


def load_profiles(base: Path, args: tuple[str, ...]) -> tuple[BarProfile, PartyProfile, ParalysisDetector]:
    own = BarProfile.model_validate_json((base / args[0]).read_text("utf-8"))
    party = PartyProfile.model_validate_json((base / args[1]).read_text("utf-8"))
    path = base / args[2]
    if not path.exists():
        # Первый профиль строится по подтверждённой разметке и предоставленной пользователем иконке.
        reference = Path(__file__).resolve().parents[1] / "assets" / "paralysis.bmp"
        target = path.parent / "paralysis.bmp"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(reference, target)
        profile = ParalysisProfile(width=party.width, height=party.height, template=target.name)
        path.write_text(profile.model_dump_json(indent=2), "utf-8")
    return own, party, ParalysisDetector(path, party)


def prepare_extension(base: Path, args: tuple[str, ...]) -> None:
    own, party, detector = load_profiles(base, args)
    TrainReader(own, party, detector)


def run_extension(controller: ActionController, base: Path, args: tuple[str, ...],
                  config: TrainConfig, notify: Callable[[str], None], deadline: float) -> None:
    own, party, detector = load_profiles(base, args)
    TrainHealer(controller, own, party, config, notify,
                base / "healer-cooldowns.json", detector).run(deadline)


class TrainHealer(Healer):
    def __init__(self, controller: ActionController, own: BarProfile, party: PartyProfile,
                 config: TrainConfig, notify: Callable[[str], None], state_path: Path,
                 detector: ParalysisDetector) -> None:
        super().__init__(controller, own, party, config, notify, state_path)
        restored = self.policy
        self.policy = TrainPolicy(config, time.monotonic())
        self.policy.ready_at = restored.ready_at
        self.policy.busy_until = restored.busy_until
        config.poll_seconds = config.collect_poll_seconds
        self.observer = TrainObserver(controller.desktop, TrainReader(own, party, detector),
                                      lambda: config.poll_seconds, controller.paused.is_set)
        self.selecting: HealDecision | None = None

    def latest(self) -> TrainSnapshot:
        state = super().latest()
        if not isinstance(state, TrainSnapshot) or time.monotonic() - state.at > .5:
            raise TargetChanged("Нет свежего наблюдения парика")
        return state

    def select(self, name: str, button: str = "left", twice: bool = False) -> int:
        x, y, slot = self.point(name)
        x, y = self.click_point(x, y, slot)

        def verify() -> None:
            state = self.latest()
            if self.point(name)[2] != slot:
                raise TargetChanged("Строка цели исчезла")
            if self.selecting is not None:
                current = self.policy.decide(state, time.monotonic())
                intended = self.selecting
                if (current.kind, current.skill, current.target) != (intended.kind, intended.skill, intended.target):
                    raise TargetChanged("Фаза парика или приоритет изменились при наведении")

        self.controller.click(button, x, y, verify=verify)
        if twice:
            self.controller.wait(.05)
            self.controller.click(button, x, y, verify=verify)
        return slot

    def execute(self, decision: HealDecision, state: HealerSnapshot) -> None:
        self.selecting = decision
        try:
            super().execute(decision, state)
        finally:
            self.selecting = None

    def recover_unknown_state(self, deadline: float) -> None:
        raise RuntimeError("Парик: данные потеряны; восстановительные движения запрещены")

    def report(self, message: str) -> None:
        if isinstance(self.policy, TrainPolicy):
            paralysis = ",".join(map(str, self.policy.paralyzed_slots)) or "нет"
            frequency = ""
            if isinstance(self.observer, TrainObserver):
                with self.observer.lock:
                    samples = tuple(self.observer.samples)
                if len(samples) > 1 and samples[-1].at > samples[0].at:
                    fps = (len(samples) - 1) / (samples[-1].at - samples[0].at)
                    frequency = f", наблюдение {fps:.1f} Гц"
            header = (f"Парик [{self.policy.phase}, танк №{self.policy.tank_slot or '?'}, "
                      f"паралич: {paralysis}{frequency}]")
            message = f"{message}\n{header}" if "\n" in message else f"{header}: {message}"
        super().report(message)
