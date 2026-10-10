"""Shared test helpers: a fake Real-ESRGAN executable and a real FFmpeg video generator.

The fake engine imitates the command line of realesrgan-ncnn-vulkan (same flags, the same
per-tile percentage output, directory mode, model-file lookup) but only resizes with Pillow.
It lets the whole pipeline - Pillow, FFmpeg, queue, cancel, errors - be tested without a GPU
and without downloading models. It is NOT a substitute for testing the real engine.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

STUB = r'''#!{python}
import os, sys, time
from pathlib import Path
from PIL import Image

args = sys.argv[1:]
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default

print("[0 Fake GPU]  queueC=0[1] queueG=0[1]", file=sys.stderr, flush=True)
inp, out = Path(opt("-i")), Path(opt("-o"))
model, scale = opt("-n"), int(opt("-s", "4"))
models = Path(opt("-m", "models"))
stem = f"{{model}}-x{{scale}}" if model == "realesr-animevideov3" else model
if not (models / (stem + ".param")).exists() or not (models / (stem + ".bin")).exists():
    print("failed to load model", stem, file=sys.stderr); sys.exit(1)
mode = os.environ.get("DAFAN_STUB_MODE", "")
if mode == "oom":
    print("vkAllocateMemory failed -2", file=sys.stderr); sys.exit(1)
if mode == "crash":
    print("boom", file=sys.stderr); sys.exit(3)
sleep = float(os.environ.get("DAFAN_STUB_SLEEP", "0"))

def up(src, dst):
    im = Image.open(src); im.load()
    im = im.resize((im.width * scale, im.height * scale), Image.BICUBIC)
    im.save(dst)

if inp.is_dir():
    for f in sorted(inp.glob("*.png")):
        if sleep: time.sleep(sleep)
        up(f, out / f.name)
        print(f"{{f.name}} -> {{f.name}} done", file=sys.stderr, flush=True)
else:
    for pct in (0, 25, 50, 75):
        print(f"{{pct:.2f}}%", file=sys.stderr, flush=True)
        if sleep: time.sleep(sleep)
    up(inp, out)
    print("100.00%", file=sys.stderr, flush=True)
'''

MODELS = ["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3-x2",
          "realesr-animevideov3-x3", "realesr-animevideov3-x4"]


def make_tools(root: Path) -> Path:
    """Create root/realesrgan/{realesrgan-ncnn-vulkan, models/*}. Returns the tools dir."""
    esr = root / "realesrgan"
    (esr / "models").mkdir(parents=True)
    exe = esr / "realesrgan-ncnn-vulkan"
    exe.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for m in MODELS:
        (esr / "models" / f"{m}.param").write_text("fake")
        (esr / "models" / f"{m}.bin").write_bytes(b"fake")
    return root


def ffmpeg_ok(encoder: str = "libx264") -> bool:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return encoder in out


def make_video(path: Path, w=64, h=48, fps=10, seconds=2, audio=True, vcodec="libx264", acodec="aac") -> Path:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
           f"testsrc=size={w}x{h}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", vcodec, "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", acodec]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def ffprobe_json(path: Path) -> dict:
    import json
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def count_frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out)


class TempCase:
    """Mixin: self.tmp (Path) is created per test and removed afterwards."""

    def setUp(self):
        from dafan import i18n
        i18n.set_language("en")  # tests assert English messages; never inherit another test's language
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        super().setUp()


def cancel_after(event: threading.Event, seconds: float) -> threading.Timer:
    timer = threading.Timer(seconds, event.set)
    timer.start()
    return timer


def env_with(**kw):
    import unittest.mock as m
    return m.patch.dict(os.environ, {k: str(v) for k, v in kw.items()})
