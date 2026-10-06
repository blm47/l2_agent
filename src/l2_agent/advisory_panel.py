import logging
import queue
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from l2_agent.advisory import AdvisoryResult, LLMProvider
from l2_agent.world_state import WorldState

logger = logging.getLogger("l2_agent.advisory")


class AdvisoryPanel(QWidget):
    def __init__(self, provider: LLMProvider,
                 snapshot: Callable[[], WorldState | None]) -> None:
        super().__init__()
        self.provider = provider
        self.snapshot = snapshot
        self.results: queue.Queue[AdvisoryResult | str] = queue.Queue(maxsize=1)
        self.busy = False
        layout = QVBoxLayout(self)
        self.goal = QLineEdit()
        self.goal.setMaxLength(2000)
        self.goal.setPlaceholderText("Цель сессии для советника")
        self.request_button = QPushButton("Запросить совет (без исполнения)")
        self.request_button.clicked.connect(self.request_advice)
        self.status = QLabel("M2: рекомендации по снимку WorldState, без управления игрой")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        for widget in (self.goal, self.request_button, self.status):
            layout.addWidget(widget)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def request_advice(self) -> None:
        if self.busy:
            return
        state = self.snapshot()
        if state is None or not 0 <= time.monotonic() - state.timestamp <= 1:
            self.status.setText("Для совета нужен свежий WorldState")
            return
        goal = self.goal.text().strip()
        if not goal:
            self.status.setText("Укажите цель сессии")
            return
        self.busy = True
        self.request_button.setEnabled(False)
        self.status.setText(f"Запрос по кадру #{state.frame_id}…")
        # Worker не обращается к Qt и не получает доступ к контроллеру ввода.
        provider, results = self.provider, self.results

        def run() -> None:
            try:
                result = provider.propose(state, goal)
            except Exception:
                logger.exception("Ошибка advisory-запроса")
                result = "Совет отклонён: ошибка сервера или невалидный ответ; см. лог"
            results.put_nowait(result)

        threading.Thread(target=run, name="advisory", daemon=True).start()

    def poll(self) -> None:
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.request_button.setEnabled(True)
        if isinstance(result, str):
            self.status.setText(result)
            return
        self.status.setText(
            f"Совет по прошлому снимку #{result.frame_id} (не исполняется): "
            f"{result.proposal.model_dump_json()}\n"
            f"{result.model}: {result.telemetry.latency_s:.2f} s; "
            f"tokens/s (end-to-end): {result.telemetry.tokens_per_second}"
        )
