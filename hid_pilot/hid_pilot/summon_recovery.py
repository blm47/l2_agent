"""
История панели пантеры и непрерывного наблюдения пати перед перепризывом.
"""


class SummonRecovery:
    def __init__(self) -> None:
        self.checked = False
        self.pending = False
        self.rebuff = False
        self.attempts = 0
        self.ready_at = float("-inf")
        self.samples = 0
        self.sample_at = float("-inf")
        self.missing_since: float | None = None
        self.observed_at = float("-inf")
        self.generation = -1
        self.quiet_since: float | None = None
        self.required_resources: set[str] = {"farmer_hp", "support_hp"}

    def invalidate(self) -> None:
        """
        Пропуск кадра сбрасывает доказательства, но не уже назначенный перепризыв.
        """
        self.missing_since = None
        self.observed_at = float("-inf")
        self.quiet_since = None
        self.samples = 0
        self.sample_at = float("-inf")

    def observe(self, resources: dict[str, float | None], now: float, generation: int,
                damaged: bool, epsilon: float) -> bool:
        """
        Возвращает True только при новом решении о необходимости призыва.
        """
        was_pending = self.pending
        continuous = self.generation == generation and 0 <= now - self.observed_at <= 1
        if not continuous:
            self.invalidate()
        self.generation = generation
        self.observed_at = now
        hp = resources.get("summon_hp")
        if hp is not None and hp > 0:
            self.checked = True
            self.missing_since = None
            self.samples = 0
        elif hp == 0:
            self.checked = self.pending = True
        elif resources.get("summon_mp") is not None:
            # Читаемая MP подтверждает панель: ошибка HP не требует перепризыва.
            self.missing_since = None
            self.samples = 0
        else:
            # Устойчивое отсутствие ресурсов требует проверки, но не доказывает смерть.
            # Последний урон мог остаться за переключением окна на саппорта.
            if self.missing_since is None:
                self.missing_since = now
            # Повтор одного кадра в одном тике не считается новой попыткой чтения.
            if now - self.sample_at >= .2 - 1e-9:
                self.samples += 1
                self.sample_at = now
            if self.samples >= 3 and (not self.checked or now - self.missing_since >= 2 - 1e-9):
                self.checked = self.pending = True

        # Самон ожидаемо отсутствует. MP не является признаком входящего урона.
        required = {"farmer_hp", "support_hp"}
        required.update(name for name, value in resources.items()
                        if not name.startswith("summon_")
                        and (name.endswith("_hp") or name == "farmer_cp"
                             or name.endswith("_cp")
                             and (value is not None or name in self.required_resources)))
        if required != self.required_resources:
            # Исчезнувшая строка больше не обязательна; новый состав проверяем с нуля.
            self.quiet_since = None
        self.required_resources = required
        complete = all(resources.get(name) is not None for name in self.required_resources)
        if not complete:
            self.quiet_since = None
        elif not continuous or damaged or self.quiet_since is None:
            self.quiet_since = now
        return self.pending and not was_pending

    def wait_reason(self, resources: dict[str, float | None], now: float,
                    seconds: float, generation: int) -> str:
        """
        Объясняет блокировку без подстановки старых или выдуманных ресурсов.
        """
        if self.generation != generation or not 0 <= now - self.observed_at <= 1:
            return "нет свежего непрерывного наблюдения"
        missing = sorted(name for name in self.required_resources if resources.get(name) is None)
        if missing:
            return "не читаются обязательные ресурсы: " + ", ".join(missing)
        if self.quiet_since is None:
            return "безопасный интервал ещё не начат"
        remaining = max(0., seconds - (now - self.quiet_since))
        if remaining > 0:
            return f"ждём {remaining:.1f} с без урона и пропусков наблюдения"
        return "безопасный интервал подтверждён"

    def quiet(self, now: float, seconds: float, generation: int) -> bool:
        return (self.generation == generation and 0 <= now - self.observed_at <= 1
                and self.quiet_since is not None and now - self.quiet_since >= seconds)
