import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from l2_agent.logging_setup import setup_logging

logger = logging.getLogger("l2_agent.training")


def main() -> int:
    parser = argparse.ArgumentParser(description="Пробное обучение детектора M1")
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/detector/export-v1")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=60)
    args = parser.parse_args()
    run = args.run.resolve()
    run.mkdir(parents=True, exist_ok=False)
    setup_logging(run / "logs")
    config = ROOT / "artifacts/ultralytics-config"
    config.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    try:
        import torch
        import ultralytics
        from ultralytics import YOLO, settings
        from ultralytics.utils import LOGGER

        LOGGER.handlers.clear()
        LOGGER.parent = logging.getLogger("l2_agent")
        LOGGER.propagate = True
        settings.update({"sync": False, "wandb": False, "mlflow": False,
                         "comet": False, "clearml": False, "tensorboard": False})
        torch.set_num_threads(4)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA недоступна; обучение на CPU автоматически не запускается")
        probe = torch.ones((64, 64), device="cuda")
        _ = probe @ probe
        torch.cuda.synchronize()
        del probe
        snapshot = run / "dataset"
        shutil.copytree(args.dataset, snapshot)
        report = json.loads((snapshot / "export.json").read_text(encoding="utf-8"))
        for record in report["records"]:
            sample = record["sample"]
            image = snapshot / "images" / record["split"] / (sample["sample_id"] + ".png")
            if hashlib.sha256(image.read_bytes()).hexdigest() != sample["image_sha256"]:
                raise ValueError("SHA-256 кадра не соответствует export.json")
        data = snapshot / "dataset.yaml"
        data.write_text(
            f"path: {json.dumps(snapshot.as_posix())}\ntrain: images/train\nval: images/val\nnames:\n"
            + "".join(f"  {i}: {name}\n" for i, name in enumerate(report["classes"])),
            encoding="utf-8",
        )
        environment = {"torch": torch.__version__, "ultralytics": ultralytics.__version__,
                       "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
                       "architectures": torch.cuda.get_arch_list(),
                       "dataset_sha256": hashlib.sha256((snapshot / "export.json").read_bytes()).hexdigest()}
        (run / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
        logger.info("Обучение на GPU: %s", environment)
        weights = ROOT / "models/yolov8n.pt"
        weights.parent.mkdir(exist_ok=True)
        model = YOLO(str(weights))
        model.train(
            data=str(data), epochs=args.epochs, patience=15, imgsz=960, batch=2,
            device=0, workers=0, cache=False, amp=False, seed=42, deterministic=True,
            optimizer="SGD", lr0=0.003, lrf=0.01, momentum=0.937, weight_decay=0.0005,
            warmup_epochs=3, mosaic=0.5, close_mosaic=10, mixup=0.0,
            fliplr=0.0, flipud=0.0, degrees=0.0, translate=0.05, scale=0.2,
            hsv_h=0.01, hsv_s=0.2, hsv_v=0.2,
            project=str(run), name="fit", exist_ok=False, plots=True, verbose=False,
        )
        best = YOLO(str(run / "fit/weights/best.pt"))
        metrics = best.val(data=str(data), imgsz=960, batch=2, device=0, workers=0,
                           half=False, project=str(run), name="validation", plots=True)
        per_class = {}
        for index, class_id in enumerate(metrics.box.ap_class_index):
            per_class[best.names[int(class_id)]] = dict(zip(
                ("precision", "recall", "map50", "map50_95"),
                map(float, metrics.box.class_result(index)),
            ))
        summary = {"overall": metrics.results_dict, "per_class": per_class,
                   "speed_ms": metrics.speed,
                   "peak_cuda_allocated_mb": torch.cuda.max_memory_allocated() / 2**20,
                   "limitation": "Только 6 val кадров из тех же сессий; не приёмка M1"}
        (run / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        for _ in best.predict(source=str(snapshot / "images/val"), imgsz=960, device=0,
                              conf=0.25, save=True, save_txt=True, save_conf=True,
                              stream=True, project=str(run), name="predictions", verbose=False):
            pass
        logger.info("Обучение завершено; веса и отчёт: %s", run)
        return 0
    except Exception:
        logger.exception("Обучение завершилось ошибкой")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
