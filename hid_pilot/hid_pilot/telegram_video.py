"""
Ограниченная по ресурсам подготовка MP4-копии для Telegram.
"""

import shutil
import subprocess
from pathlib import Path

from hid_pilot.paths import user_data_dir
from hid_pilot.replay import REPLAY_SECONDS

MAX_VIDEO_BYTES = 45_000_000
PLAYBACK_SPEED = 2


def prepare_video(source: Path, destination: Path) -> None:
    encoder = shutil.which("ffmpeg")
    if encoder is None:
        bundled = user_data_dir() / "tools" / "ffmpeg.exe"
        if not bundled.is_file():
            raise ValueError("Для видео Telegram нужен FFmpeg в PATH или HIDPilot/tools/ffmpeg.exe")
        encoder = str(bundled)
    # Исходный ролик сохраняется; два CPU-потока ограничивают нагрузку на игровой компьютер.
    command = [encoder, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-filter_threads", "1", "-threads", "2", "-sseof", str(-REPLAY_SECONDS),
               "-i", str(source), "-map", "0:v:0", "-an",
               "-vf", f"scale=w='min(1920,iw)':h=-2,setpts=(PTS-STARTPTS)/{PLAYBACK_SPEED}",
               "-r", str(15 * PLAYBACK_SPEED), "-c:v", "libx264",
               "-preset", "veryfast", "-threads", "2", "-b:v", "2400k",
               "-maxrate", "2600k", "-bufsize", "5200k", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", "-t", str(REPLAY_SECONDS / PLAYBACK_SPEED), str(destination)]
    subprocess.run(command, check=True, timeout=180, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not destination.is_file() or not 0 < destination.stat().st_size <= MAX_VIDEO_BYTES:
        raise ValueError("MP4 для Telegram отсутствует, пуст или превышает ограничение размера")
