import argparse
import json
import logging
import platform
import subprocess
import time
from pathlib import Path

from l2_agent.advisory import LocalChatProvider
from l2_agent.logging_setup import setup_logging
from l2_agent.world_state import WorldState


def gpu_snapshot() -> str | None:
    try:
        return subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            text=True, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="M2 advisory benchmark без игрового ввода")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="Ternary-Bonsai-2-27B-PTQ1_0.gguf")
    parser.add_argument("--state", type=Path, help="JSON WorldState; по умолчанию unknown fixture")
    parser.add_argument("--goal", default="Оцени состояние; уточни недостающие данные")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("artifacts/m2-benchmark.json"))
    args = parser.parse_args()
    if not 1 <= args.runs <= 30:
        parser.error("runs должен быть от 1 до 30")
    setup_logging()
    logger = logging.getLogger("l2_agent.benchmark")
    state = (WorldState.model_validate_json(args.state.read_text(encoding="utf-8"))
             if args.state else WorldState(frame_id=0, timestamp=time.monotonic()))
    provider = LocalChatProvider(args.model, args.url)
    report = {"model": args.model, "platform": platform.platform(),
              "input": str(args.state) if args.state else "synthetic_unknown",
              "gpu_before": gpu_snapshot(), "runs": []}
    for index in range(args.runs):
        started = time.perf_counter()
        try:
            result = provider.propose(state, args.goal)
            report["runs"].append({"ok": True, "result": result.model_dump()})
        except Exception as exc:
            logger.exception("Benchmark: запрос %s завершился ошибкой", index + 1)
            report["runs"].append({"ok": False, "error": str(exc),
                                   "latency_s": time.perf_counter() - started})
    report["gpu_after"] = gpu_snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Benchmark сохранён: %s", args.output)
    return 0 if all(run["ok"] for run in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
