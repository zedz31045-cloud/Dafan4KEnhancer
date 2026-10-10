"""Image enhancement: Pillow I/O + optional OpenCV denoise + Real-ESRGAN (ncnn)."""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from .config import Settings
from .i18n import t
from .media import free_bytes, safe_stem, unique_path
from .toolchain import Toolchain, has_native_scale, model_installed
from .util import (Cancelled, ProcessingError, friendly_failure, last_percent,
                   run_cancellable, tail)

MAX_OUTPUT_MP = 400  # refuse outputs above this many megapixels (RAM safety)

PRESETS = {
    # preset: (model, default denoise)
    "general":   ("realesrgan-x4plus", "none"),
    "portrait":  ("realesrgan-x4plus", "light"),
    "landscape": ("realesrgan-x4plus", "none"),
    "old":       ("realesrgan-x4plus", "medium"),
    "anime":     ("realesrgan-x4plus-anime", "none"),
    "fast":      ("realesr-animevideov3", "none"),
}
DENOISE_H = {"none": 0, "light": 3, "medium": 6, "strong": 10}
Progress = Callable[[float], None]


@dataclass
class ImageParams:
    model: str
    scale: int
    denoise: str
    fmt: str
    jpg_quality: int
    tile: int
    device: str

    @classmethod
    def from_settings(cls, s: Settings) -> "ImageParams":
        model, preset_denoise = PRESETS.get(s.image_preset, PRESETS["general"])
        denoise = preset_denoise if s.image_denoise == "auto" else s.image_denoise
        return cls(model, s.image_scale, denoise, s.image_format, s.jpg_quality,
                   s.tile_size(), s.device)

    def models_needed(self) -> list[tuple[str, int]]:
        native = has_native_scale(self.model, self.scale)
        return [(self.model, self.scale if native else 4)]


def output_size(w: int, h: int, scale: int) -> tuple[int, int]:
    return w * scale, h * scale


def estimate_output_bytes(w: int, h: int, scale: int, fmt: str, src_ext: str) -> int:
    """Rough size guess (compressed) for the 'expected size' bar."""
    px = w * h * scale * scale
    ext = src_ext.lower() if fmt == "same" else "." + fmt
    per_px = {".png": 2.2, ".jpg": 0.45, ".jpeg": 0.45, ".webp": 0.35,
              ".bmp": 3.0, ".tif": 2.6, ".tiff": 2.6}.get(ext, 1.5)
    return int(px * per_px)


def resolve_format(fmt: str, src: Path) -> tuple[str, str]:
    """Return (pillow_format, extension) for the output."""
    if fmt == "same":
        ext = src.suffix.lower()
        fmt = {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".webp": "webp", ".bmp": "bmp",
               ".tif": "tiff", ".tiff": "tiff"}.get(ext, "png")
    table = {"png": ("PNG", ".png"), "jpg": ("JPEG", ".jpg"), "webp": ("WEBP", ".webp"),
             "bmp": ("BMP", ".bmp"), "tiff": ("TIFF", ".tiff")}
    return table[fmt]


def _normalize(img: Image.Image) -> Image.Image:
    """8-bit RGB or RGBA, honouring EXIF orientation. Never touches the source file."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16L", "I;16B", "I"):
        arr = np.asarray(img, dtype=np.float64)
        if arr.max() > 255:
            arr = arr / 256.0
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L").convert("RGB")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        return img.convert("RGBA")
    return img.convert("RGB")


def _denoise(img: Image.Image, level: str) -> Image.Image:
    h = DENOISE_H.get(level, 0)
    if not h:
        return img
    import cv2
    arr = np.asarray(img)
    if arr.shape[2] == 4:
        rgb = cv2.fastNlMeansDenoisingColored(np.ascontiguousarray(arr[:, :, :3]), None, h, h, 7, 21)
        arr = np.dstack([rgb, arr[:, :, 3]])
    else:
        arr = cv2.fastNlMeansDenoisingColored(np.ascontiguousarray(arr), None, h, h, 7, 21)
    return Image.fromarray(arr)


def _save(img: Image.Image, dst: Path, pil_format: str, quality: int, icc, exif) -> None:
    kwargs: dict = {}
    if pil_format == "JPEG":
        if img.mode == "RGBA":  # JPEG has no alpha: flatten on white
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        kwargs.update(quality=quality, subsampling=0, optimize=True)
    elif pil_format == "WEBP":
        kwargs.update(quality=quality, method=4)
    elif pil_format == "PNG":
        kwargs.update(compress_level=6)
    elif pil_format == "TIFF":
        kwargs.update(compression="tiff_lzw")
    elif pil_format == "BMP" and img.mode == "RGBA":
        img = img.convert("RGB")
    if icc and pil_format in ("JPEG", "PNG", "WEBP", "TIFF"):
        kwargs["icc_profile"] = icc
    if exif and pil_format in ("JPEG", "WEBP"):
        kwargs["exif"] = exif
    img.save(dst, format=pil_format, **kwargs)


def _device_args(device: str) -> list[str]:
    if device == "cpu":
        return ["-g", "-1"]
    if device.isdigit():
        return ["-g", device]
    return []  # auto


def enhance_image(src: Path, out_dir: Path, params: ImageParams, tc: Toolchain,
                  cancel: threading.Event, progress: Progress, workdir: Path) -> Path:
    """Enhance one image. The original is only ever read. Returns the output path."""
    if not tc.realesrgan:
        raise ProcessingError(t("err_tool_missing", tool="Real-ESRGAN"))
    native = has_native_scale(params.model, params.scale)
    run_scale = params.scale if native else 4
    if not model_installed(tc, params.model, run_scale):
        raise ProcessingError(t("err_model_missing", model=f"{params.model} x{run_scale}"))

    try:
        with Image.open(src) as im:
            im.load()
            icc = im.info.get("icc_profile")
            # Normalize first: exif_transpose reads the orientation tag from the same cached
            # Exif object, so the tag may only be removed afterwards.
            base = _normalize(im)
            exif_obj = Image.Exif()
            raw_exif = im.info.get("exif")
            if raw_exif:
                exif_obj.load(raw_exif)
                exif_obj.pop(274, None)  # orientation is already applied to the pixels
            exif = exif_obj.tobytes() if len(exif_obj) else None
    except Exception as exc:
        raise ProcessingError(t("err_read_image", err=f"{type(exc).__name__}: {exc}")) from exc

    w, h = base.size
    ow, oh = output_size(w, h, params.scale)
    if ow * oh > MAX_OUTPUT_MP * 1_000_000:
        raise ProcessingError(t("err_image_too_big", w=ow, h=oh, mp=MAX_OUTPUT_MP))
    pil_format, ext = resolve_format(params.fmt, src)
    out_dir.mkdir(parents=True, exist_ok=True)
    need = ow * oh * 3 * 4 + w * h * 3 * 2 + 200 * 1024 * 1024
    if free_bytes(workdir) < need or free_bytes(out_dir) < ow * oh * 4:
        raise ProcessingError(t("err_disk"))

    job_dir = workdir / f"img_{uuid.uuid4().hex[:12]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        progress(0.02)
        pre = job_dir / "in.png"
        _denoise(base, params.denoise).save(pre, format="PNG", compress_level=1)
        del base
        if cancel.is_set():
            raise Cancelled()
        progress(0.10)

        raw = job_dir / "out.png"
        log = job_dir / "esrgan.log"
        cmd = [tc.realesrgan, "-i", pre, "-o", raw, "-n", params.model, "-s", run_scale,
               "-t", params.tile, "-f", "png", "-m", tc.models_dir, *_device_args(params.device)]

        def tick():
            pct = last_percent(tail(log, 1500))
            if pct is not None:
                progress(0.10 + 0.80 * pct / 100.0)

        rc = run_cancellable(cmd, cancel, log, tick)
        if rc != 0 or not raw.is_file():
            hint = friendly_failure(tail(log))
            detail = tail(log, 400).strip()
            raise ProcessingError(t(hint) if hint else t("err_engine", code=rc, detail=detail))
        progress(0.92)

        with Image.open(raw) as res:
            res.load()
            result = res.convert("RGBA" if res.mode == "RGBA" else "RGB")
        if (result.width, result.height) != (ow, oh):
            result = result.resize((ow, oh), Image.LANCZOS)
        if cancel.is_set():
            raise Cancelled()

        stem = safe_stem(src.stem)
        dst = unique_path(out_dir, f"{stem}_dafan_{params.scale}x", ext)
        tmp = dst.with_name(dst.stem + ".partial" + dst.suffix)
        try:
            _save(result, tmp, pil_format, params.jpg_quality, icc, exif)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
        progress(1.0)
        return dst
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
