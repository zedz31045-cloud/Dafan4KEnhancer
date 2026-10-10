"""Video enhancement.

Two clearly separate modes:

* ``ai``      - frames are extracted with FFmpeg, upscaled by a Real-ESRGAN model,
                then re-encoded with FFmpeg. Work is done in small chunks so disk
                usage stays bounded and nothing is ever loaded fully into RAM.
* ``classic`` - ordinary FFmpeg Lanczos resizing. This is NOT AI enhancement; it
                only changes the dimensions and adds no new detail.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .config import Settings
from .i18n import t
from .media import (MP4_COPY_AUDIO, VideoInfo, ai_scale_for, check_video_target, free_bytes,
                    safe_stem, unique_path, video_target_size)
from .toolchain import Toolchain, ffmpeg_has_encoder, model_installed
from .util import Cancelled, ProcessingError, friendly_failure, run_cancellable, tail

Progress = Callable[[float], None]
VIDEO_AI_MODELS = ("realesr-animevideov3", "realesrgan-x4plus", "realesrgan-x4plus-anime")


@dataclass
class VideoParams:
    mode: str
    target: str
    custom_w: int
    custom_h: int
    codec: str
    crf: int
    bitrate_kbps: int
    fps: str
    audio_mode: str
    container: str
    chunk_frames: int
    model: str
    tile: int
    device: str

    @classmethod
    def from_settings(cls, s: Settings) -> "VideoParams":
        return cls(s.video_mode, s.video_target, s.custom_w, s.custom_h, s.video_codec, s.video_crf,
                   s.video_bitrate_kbps, s.video_fps, s.audio_mode, s.container, s.chunk_frames,
                   s.video_model, s.tile_size(), s.device)


@dataclass
class VideoPlan:
    out_w: int
    out_h: int
    pad: bool
    ai_scale: int
    fps_out: Fraction
    total_frames: int


def make_plan(info: VideoInfo, p: VideoParams) -> VideoPlan:
    out_w, out_h, pad = video_target_size(info.width, info.height, p.target, p.custom_w, p.custom_h)
    check_video_target(info, out_w, out_h)
    fps_out = info.fps if p.fps == "original" else Fraction(int(p.fps))
    seconds = info.duration if info.duration > 0 else info.frames / float(info.fps)
    total = max(1, int(round(seconds * float(fps_out))))
    scale = ai_scale_for(info.width, info.height, out_w, out_h) if p.model == "realesr-animevideov3" else 4
    return VideoPlan(out_w, out_h, pad, scale, fps_out, total)


def models_needed(info: VideoInfo, p: VideoParams) -> list[tuple[str, int]]:
    if p.mode != "ai":
        return []
    out_w, out_h, _ = video_target_size(info.width, info.height, p.target, p.custom_w, p.custom_h)
    scale = ai_scale_for(info.width, info.height, out_w, out_h) if p.model == "realesr-animevideov3" else 4
    return [(p.model, scale)]


def estimate_video_bytes(info: VideoInfo, plan: VideoPlan, p: VideoParams) -> int:
    seconds = plan.total_frames / float(plan.fps_out)
    if p.bitrate_kbps > 0:
        return int(p.bitrate_kbps * 1000 / 8 * seconds)
    bits_per_px = 0.06 * 2 ** ((18 - p.crf) / 6) * (0.7 if p.codec == "h265" else 1.0)
    return int(plan.out_w * plan.out_h * float(plan.fps_out) * seconds * bits_per_px / 8)


def _fps_str(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


def scale_filter(plan: VideoPlan) -> str:
    base = f"scale={plan.out_w}:{plan.out_h}"
    if plan.pad:
        return (f"{base}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={plan.out_w}:{plan.out_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    return f"{base}:flags=lanczos,setsar=1"


def video_encode_args(p: VideoParams) -> list[str]:
    if p.codec == "h265":
        args = ["-c:v", "libx265", "-preset", "medium", "-x265-params", "log-level=error"]
    else:
        args = ["-c:v", "libx264", "-preset", "medium"]
    if p.bitrate_kbps > 0:
        k = p.bitrate_kbps
        args += ["-b:v", f"{k}k", "-maxrate", f"{int(k * 1.5)}k", "-bufsize", f"{k * 2}k"]
    else:
        args += ["-crf", str(p.crf)]
    return args + ["-pix_fmt", "yuv420p"]


def audio_args(p: VideoParams, info: VideoInfo) -> list[str]:
    if p.audio_mode == "none" or not info.has_audio:
        return ["-an"]
    # Codec arguments only; the caller adds the matching "-map ...:a?".
    # MP4 cannot hold every audio codec (e.g. Opus from WEBM), so fall back to AAC there.
    copy_ok = p.container == "mkv" or info.audio_codec in MP4_COPY_AUDIO
    if p.audio_mode == "copy" and copy_ok:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]


def _concat_line(path: Path) -> str:
    """One line of an FFmpeg concat list; a single quote inside the path is written as '\\''."""
    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def _base_cmd(ffmpeg: Path) -> list:
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]


def _fail(log: Path, rc: int) -> ProcessingError:
    text = tail(log)
    hint = friendly_failure(text)
    return ProcessingError(t(hint) if hint else t("err_engine", code=rc, detail=text.strip()[-400:]))


def _device_args(device: str) -> list[str]:
    if device == "cpu":
        return ["-g", "-1"]
    return ["-g", device] if device.isdigit() else []


def _out_time(progress_file: Path) -> float | None:
    text = tail(progress_file, 800)
    m = re.findall(r"out_time_(?:us|ms)=(\d+)", text)
    return int(m[-1]) / 1_000_000 if m else None


def enhance_video(src: Path, out_dir: Path, info: VideoInfo, p: VideoParams, tc: Toolchain,
                  cancel: threading.Event, progress: Progress, workdir: Path) -> Path:
    if not (tc.ffmpeg and tc.ffprobe):
        raise ProcessingError(t("err_tool_missing", tool="FFmpeg"))
    enc = "libx265" if p.codec == "h265" else "libx264"
    if not ffmpeg_has_encoder(tc.ffmpeg, enc):
        raise ProcessingError(t("err_encoder", enc=enc))
    plan = make_plan(info, p)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "ai" if p.mode == "ai" else "classic"
    stem = safe_stem(src.stem)
    dst = unique_path(out_dir, f"{stem}_dafan_{tag}_{plan.out_w}x{plan.out_h}", "." + p.container)
    partial = dst.with_name(dst.stem + ".partial" + dst.suffix)
    job_dir = workdir / f"vid_{uuid.uuid4().hex[:12]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    log = job_dir / "run.log"
    try:
        if p.mode == "classic":
            _classic(src, partial, info, p, plan, tc, cancel, progress, job_dir, log)
        else:
            _ai(src, partial, info, p, plan, tc, cancel, progress, job_dir, log)
        if cancel.is_set():
            raise Cancelled()
        os.replace(partial, dst)
        progress(1.0)
        return dst
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(job_dir, ignore_errors=True)


def _mux_tail(p: VideoParams) -> list[str]:
    extra: list[str] = []
    if p.container == "mp4":
        extra += ["-movflags", "+faststart"]
        if p.codec == "h265":
            extra += ["-tag:v", "hvc1"]
    return extra


def _classic(src, dst, info, p, plan, tc, cancel, progress, job_dir, log):
    prog = job_dir / "progress.txt"
    vf = scale_filter(plan)
    if p.fps != "original":
        vf = f"fps={_fps_str(plan.fps_out)}," + vf
    cmd = [*_base_cmd(tc.ffmpeg), "-progress", prog, "-i", src, "-map", "0:v:0"]
    if p.audio_mode != "none" and info.has_audio:
        cmd += ["-map", "0:a?"]
    cmd += ["-vf", vf, *video_encode_args(p)]
    cmd += audio_args(p, info) + _mux_tail(p) + [dst]
    duration = info.duration or plan.total_frames / float(plan.fps_out)

    def tick():
        t_done = _out_time(prog)
        if t_done is not None and duration > 0:
            progress(min(0.99, t_done / duration))

    rc = run_cancellable(cmd, cancel, log, tick)
    if rc != 0 or not dst.is_file():
        raise _fail(log, rc)


def _count_png(folder: Path) -> int:
    try:
        return sum(1 for e in os.scandir(folder) if e.name.endswith(".png"))
    except OSError:
        return 0


def _ai(src, dst, info, p, plan, tc, cancel, progress, job_dir, log):
    if not tc.realesrgan:
        raise ProcessingError(t("err_tool_missing", tool="Real-ESRGAN"))
    if not model_installed(tc, p.model, plan.ai_scale):
        raise ProcessingError(t("err_model_missing", model=f"{p.model} x{plan.ai_scale}"))
    fps_s = _fps_str(plan.fps_out)
    chunks: list[Path] = []
    done = 0
    total = plan.total_frames
    in_dir, out_dir = job_dir / "in", job_dir / "out"
    src_px = info.width * info.height
    out_px = plan.out_w * plan.out_h
    ai_px = src_px * plan.ai_scale ** 2
    chunk_n = 0
    while done < total:
        n = min(p.chunk_frames, total - done)
        need = n * (src_px + ai_px) * 3 + n * out_px + 300 * 1024 * 1024
        if free_bytes(job_dir) < need:
            raise ProcessingError(t("err_disk_chunk", need=f"{need / 2**30:.1f} GB"))
        for d in (in_dir, out_dir):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)

        def overall(phase_start: float, phase_len: float, frac: float) -> None:
            progress(min(0.99, (done + n * (phase_start + phase_len * frac)) / total))

        # 1) extract exactly n frames at the output frame rate (constant frame rate)
        start = float(Fraction(done) / plan.fps_out)
        rc = run_cancellable(
            [*_base_cmd(tc.ffmpeg), "-ss", f"{start:.6f}", "-i", src, "-map", "0:v:0", "-an", "-sn",
             "-vf", f"fps={fps_s}", "-frames:v", n, "-start_number", 1, in_dir / "%08d.png"],
            cancel, log, lambda: overall(0.0, 0.10, min(1.0, _count_png(in_dir) / n)))
        if rc != 0:
            raise _fail(log, rc)
        got = _count_png(in_dir)
        if got == 0:
            break
        # 2) AI upscale the extracted frames (directory mode)
        rc = run_cancellable(
            [tc.realesrgan, "-i", in_dir, "-o", out_dir, "-n", p.model, "-s", plan.ai_scale,
             "-t", p.tile, "-f", "png", "-m", tc.models_dir, *_device_args(p.device)],
            cancel, log, lambda: overall(0.10, 0.75, min(1.0, _count_png(out_dir) / got)))
        if rc != 0 or _count_png(out_dir) < got:
            raise _fail(log, rc)
        shutil.rmtree(in_dir, ignore_errors=True)
        # 3) encode this chunk (video only) to its final size and codec
        chunk = job_dir / f"chunk_{chunk_n:05d}.mkv"
        rc = run_cancellable(
            [*_base_cmd(tc.ffmpeg), "-framerate", fps_s, "-start_number", 1, "-i", out_dir / "%08d.png",
             "-vf", scale_filter(plan), *video_encode_args(p), "-an", chunk],
            cancel, log, lambda: overall(0.85, 0.15, 0.5))
        if rc != 0 or not chunk.is_file():
            raise _fail(log, rc)
        shutil.rmtree(out_dir, ignore_errors=True)
        chunks.append(chunk)
        chunk_n += 1
        done += got
        if got < n:  # source ended earlier than the probe predicted
            break
    if not chunks:
        raise ProcessingError(t("err_no_frames"))

    # 4) join chunks (stream copy) and add the original audio
    listing = job_dir / "chunks.txt"
    listing.write_text("".join(_concat_line(c) for c in chunks), encoding="utf-8")
    cmd = [*_base_cmd(tc.ffmpeg), "-f", "concat", "-safe", "0", "-i", listing, "-i", src,
           "-map", "0:v:0", "-c:v", "copy"]
    if p.audio_mode != "none" and info.has_audio:
        cmd += ["-map", "1:a?"]
    cmd += audio_args(p, info) + _mux_tail(p) + [dst]
    rc = run_cancellable(cmd, cancel, log, None)
    if rc != 0 or not dst.is_file():
        raise _fail(log, rc)
