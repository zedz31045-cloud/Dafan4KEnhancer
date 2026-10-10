"""Locating, checking and downloading the external tools (Real-ESRGAN, FFmpeg)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import resource_dir, tools_dir
from .util import Cancelled, ProcessingError, run_quiet

EXE = ".exe" if os.name == "nt" else ""
REALESRGAN_NAME = "realesrgan-ncnn-vulkan" + EXE
Progress = Callable[[int, int], None]  # (bytes_done, bytes_total)


@dataclass
class Toolchain:
    realesrgan: Path | None
    ffmpeg: Path | None
    ffprobe: Path | None

    @property
    def models_dir(self) -> Path | None:
        return self.realesrgan.parent / "models" if self.realesrgan else None


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value and Path(value).is_file() else None


def locate() -> Toolchain:
    """Find the tools. Order: environment override, bundled tools folder, PATH (FFmpeg only)."""
    base = tools_dir()
    esr = _env_path("DAFAN_REALESRGAN")
    if not esr:
        cand = base / "realesrgan" / REALESRGAN_NAME
        esr = cand if cand.is_file() else None
    ff = _env_path("DAFAN_FFMPEG")
    fp = _env_path("DAFAN_FFPROBE")
    if not ff:
        cand = base / "ffmpeg" / ("ffmpeg" + EXE)
        ff = cand if cand.is_file() else (Path(p) if (p := shutil.which("ffmpeg")) else None)
    if not fp:
        cand = base / "ffmpeg" / ("ffprobe" + EXE)
        fp = cand if cand.is_file() else (Path(p) if (p := shutil.which("ffprobe")) else None)
    return Toolchain(esr, ff, fp)


def model_files(model: str, scale: int) -> tuple[str, str]:
    """File names (.param, .bin) the ncnn build expects for a model/scale."""
    stem = f"{model}-x{scale}" if model == "realesr-animevideov3" else model
    return stem + ".param", stem + ".bin"


def model_installed(tc: Toolchain, model: str, scale: int) -> bool:
    if not tc.models_dir:
        return False
    return all((tc.models_dir / f).is_file() for f in model_files(model, scale))


def has_native_scale(model: str, scale: int) -> bool:
    """True if the model ships a dedicated file for this scale (no resize needed)."""
    return model == "realesr-animevideov3" or scale == 4


def ffmpeg_has_encoder(ffmpeg: Path, name: str) -> bool:
    try:
        proc = run_quiet([ffmpeg, "-hide_banner", "-encoders"], timeout=30)
    except Exception:
        return False
    return re.search(rf"\b{re.escape(name)}\b", proc.stdout.decode("utf-8", "replace")) is not None


def missing_tools(kind: str, tc: Toolchain, models: list[tuple[str, int]] | None = None) -> list[str]:
    """Names of what is missing before a job of this kind can start."""
    missing: list[str] = []
    if not tc.realesrgan and (kind == "image" or models):
        missing.append("realesrgan")
    if kind == "video" and not (tc.ffmpeg and tc.ffprobe):
        missing.append("ffmpeg")
    if tc.realesrgan:
        for model, scale in models or []:
            if not model_installed(tc, model, scale):
                missing.append(f"model:{model}-x{scale}")
    return missing


def list_gpus(tc: Toolchain) -> list[str]:
    """Ask the ncnn build which Vulkan devices it sees (parsed from its start-up log)."""
    if not tc.realesrgan:
        return []
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "p.png", Path(tmp) / "o.png"
        Image.new("RGB", (16, 16), (90, 90, 90)).save(src)
        model = next((m for m in ("realesrgan-x4plus", "realesr-animevideov3-x4")
                      if (tc.models_dir / f"{m}.param").is_file()), None)
        if not model:
            return []
        name, scale = ("realesrgan-x4plus", "4") if model == "realesrgan-x4plus" else ("realesr-animevideov3", "4")
        try:
            proc = run_quiet([tc.realesrgan, "-i", src, "-o", dst, "-n", name, "-s", scale,
                              "-m", tc.models_dir], timeout=90)
        except Exception:
            return []
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return [f"{i}: {n.strip()}" for i, n in re.findall(r"^\[(\d+)\s+([^\]]+)\]", text, re.M)]


# ---------------------------------------------------------------- downloading

def load_manifest() -> dict:
    for base in (resource_dir(), Path(__file__).resolve().parents[2]):
        p = base / "tools_manifest.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise ProcessingError("tools_manifest.json not found")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path, progress: Progress | None = None,
                  cancel: threading.Event | None = None, expected_sha256: str = "") -> str:
    """Download with progress, size check and optional SHA-256 check. Returns the SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Dafan4KEnhancer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel and cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise ProcessingError(f"incomplete download ({done} of {total} bytes)")
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    digest = sha256_of(part)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        part.unlink(missing_ok=True)
        raise ProcessingError(f"SHA-256 mismatch for {url}: got {digest}")
    os.replace(part, dest)
    return digest


def _safe_extract(zf: zipfile.ZipFile, member: zipfile.ZipInfo, target: Path, strip: str) -> None:
    name = member.filename
    if strip and name.startswith(strip):
        name = name[len(strip):]
    if not name or name.endswith("/"):
        return
    out = (target / name).resolve()
    if target.resolve() not in out.parents:
        raise ProcessingError(f"unsafe path in archive: {member.filename}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)


def _extract_zip(zip_path: Path, target: Path, wanted_exe: str, only_bin: bool = False) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            raise ProcessingError(f"corrupt archive entry: {bad}")
        names = zf.namelist()
        exe_entry = next((n for n in names if n.lower().endswith("/" + wanted_exe.lower())
                          or n.lower() == wanted_exe.lower()), None)
        if not exe_entry:
            raise ProcessingError(f"{wanted_exe} not found in archive")
        strip = exe_entry[: len(exe_entry) - len(wanted_exe)]
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for member in zf.infolist():
            if not member.filename.startswith(strip):
                continue
            rest = member.filename[len(strip):]
            if only_bin and "/" in rest:
                continue
            _safe_extract(zf, member, target, strip)


def install_realesrgan(progress: Progress | None = None, cancel: threading.Event | None = None,
                       dest_root: Path | None = None) -> Path:
    info = load_manifest()["realesrgan"]
    root = dest_root or tools_dir()
    zip_path = root / "_downloads" / "realesrgan.zip"
    digest = download_file(info["url"], zip_path, progress, cancel, info.get("sha256", ""))
    target = root / "realesrgan"
    _extract_zip(zip_path, target, REALESRGAN_NAME)
    zip_path.unlink(missing_ok=True)
    exe = target / REALESRGAN_NAME
    if os.name != "nt":
        exe.chmod(0o755)
    print(f"[tools] realesrgan sha256={digest}")
    return exe


def install_ffmpeg(progress: Progress | None = None, cancel: threading.Event | None = None,
                   dest_root: Path | None = None) -> Path:
    info = load_manifest()["ffmpeg"]
    root = dest_root or tools_dir()
    zip_path = root / "_downloads" / "ffmpeg.zip"
    digest = download_file(info["url"], zip_path, progress, cancel, info.get("sha256", ""))
    target = root / "ffmpeg"
    _extract_zip(zip_path, target, "ffmpeg" + EXE, only_bin=False)
    # the license text sits outside bin/ in the archive: keep it next to the binaries
    with zipfile.ZipFile(zip_path) as zf:
        lic = next((n for n in zf.namelist() if n.rsplit("/", 1)[-1].upper() == "LICENSE.TXT"), None)
        if lic:
            (target / "LICENSE-ffmpeg.txt").write_bytes(zf.read(lic))
    zip_path.unlink(missing_ok=True)
    print(f"[tools] ffmpeg sha256={digest}")
    return target / ("ffmpeg" + EXE)


def verify_installed(tc: Toolchain) -> list[str]:
    """Run the tools to prove they start. Returns a list of problems (empty = OK)."""
    problems: list[str] = []
    if tc.ffmpeg:
        p = run_quiet([tc.ffmpeg, "-version"], timeout=30)
        if p.returncode != 0:
            problems.append("ffmpeg does not start")
    else:
        problems.append("ffmpeg missing")
    if tc.ffprobe:
        p = run_quiet([tc.ffprobe, "-version"], timeout=30)
        if p.returncode != 0:
            problems.append("ffprobe does not start")
    else:
        problems.append("ffprobe missing")
    if not tc.realesrgan:
        problems.append("realesrgan missing")
    else:
        for model, scale in (("realesrgan-x4plus", 4), ("realesr-animevideov3", 2)):
            if not model_installed(tc, model, scale):
                problems.append(f"model missing: {model} x{scale}")
    return problems
