"""File-type checks, probing and output-size planning."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image

from .util import ProcessingError, run_quiet

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
MP4_COPY_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}
MAX_VIDEO_W, MAX_VIDEO_H = 7680, 4320

# Pillow's own decompression-bomb guard is replaced by our explicit megapixel limits.
Image.MAX_IMAGE_PIXELS = None


def kind_of(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    i = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{i}{suffix}"
        i += 1
    return candidate


def probe_image(path: Path) -> tuple[int, int, str]:
    """Return (width, height, format) reading only the header."""
    try:
        with Image.open(path) as im:
            return im.width, im.height, (im.format or path.suffix.lstrip(".")).upper()
    except Exception as exc:  # corrupt / unsupported file
        raise ProcessingError(f"{type(exc).__name__}: {exc}") from exc


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: Fraction
    duration: float
    frames: int
    vcodec: str
    has_audio: bool
    audio_codec: str


def _parse_fraction(text: str) -> Fraction:
    try:
        f = Fraction(text)
        return f if f > 0 else Fraction(0)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def probe_video(path: Path, ffprobe: Path) -> VideoInfo:
    proc = run_quiet([ffprobe, "-v", "error", "-print_format", "json", "-show_streams",
                      "-show_format", path], timeout=60)
    if proc.returncode != 0:
        raise ProcessingError(proc.stderr.decode("utf-8", "replace").strip()[-300:] or "ffprobe failed")
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic", 0) != 1), None)
    if not v:
        raise ProcessingError("no video stream")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = _parse_fraction(v.get("avg_frame_rate", "0")) or _parse_fraction(v.get("r_frame_rate", "0"))
    if not fps:
        fps = Fraction(30)
    duration = 0.0
    for src in (v.get("duration"), data.get("format", {}).get("duration")):
        try:
            duration = float(src)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    frames = 0
    try:
        frames = int(v.get("nb_frames", 0))
    except (TypeError, ValueError):
        frames = 0
    if frames <= 0 and duration > 0:
        frames = max(1, int(round(duration * float(fps))))
    return VideoInfo(
        width=int(v["width"]), height=int(v["height"]), fps=fps, duration=duration,
        frames=frames, vcodec=v.get("codec_name", ""), has_audio=a is not None,
        audio_codec=(a or {}).get("codec_name", ""),
    )


def _even(n: float) -> int:
    n = int(round(n))
    return max(2, n - (n % 2))


PRESET_SHORT_SIDE = {"1080p": 1080, "1440p": 1440, "2160p": 2160}


def video_target_size(src_w: int, src_h: int, preset: str, custom_w: int, custom_h: int):
    """Return (width, height, pad). Presets set the SHORT side (1080p => 1080 for
    landscape or portrait) and keep the aspect ratio; 'custom' fits inside the
    given box and pads with black bars so the aspect ratio is never distorted."""
    if preset == "custom":
        return _even(custom_w), _even(custom_h), True
    short = PRESET_SHORT_SIDE.get(preset, 2160)
    if src_w >= src_h:
        return _even(src_w * short / src_h), _even(short), False
    return _even(short), _even(src_h * short / src_w), False


def check_video_target(info: VideoInfo, out_w: int, out_h: int) -> None:
    from .i18n import t
    if out_w > MAX_VIDEO_W or out_h > MAX_VIDEO_H:
        raise ProcessingError(t("err_target_too_big", w=out_w, h=out_h))
    if out_w <= info.width and out_h <= info.height:
        raise ProcessingError(t("err_not_larger", sw=info.width, sh=info.height, w=out_w, h=out_h))


def ai_scale_for(src_w: int, src_h: int, out_w: int, out_h: int) -> int:
    """Smallest supported model scale (2, 3 or 4) that reaches the target."""
    need = max(out_w / src_w, out_h / src_h)
    for s in (2, 3, 4):
        if s >= need:
            return s
    return 4


def free_bytes(folder: Path) -> int:
    import shutil
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_stem(stem: str) -> str:
    return _BAD_NAME.sub("_", stem).strip(" .") or "file"


def is_writable_dir(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        return os.access(folder, os.W_OK)
    except OSError:
        return False


def extract_frame(ffmpeg: Path, video: Path, seconds: float, dest: Path) -> Path:
    """Save one frame of a video as PNG (used for previews and comparisons)."""
    proc = run_quiet([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                      "-ss", f"{max(0.0, seconds):.3f}", "-i", video, "-frames:v", "1", dest], timeout=120)
    if proc.returncode != 0 or not dest.is_file():
        raise ProcessingError(proc.stderr.decode("utf-8", "replace").strip()[-300:] or "frame extraction failed")
    return dest


def collect_media_files(paths, limit: int = 5000) -> list[Path]:
    """Expand dropped/selected paths: folders are searched recursively for supported media."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for root, _dirs, files in os.walk(p):
                for name in sorted(files):
                    if kind_of(name):
                        found.append(Path(root) / name)
                        if len(found) >= limit:
                            return found
        else:
            found.append(p)  # unsupported / missing files are reported by the queue
    return found
