"""Download Real-ESRGAN (ncnn-vulkan, with models) and FFmpeg into ./tools and verify them.

Usage:  python scripts/fetch_tools.py [--dest tools] [--skip-ffmpeg] [--skip-esrgan]
Hashes are printed so they can be pinned in tools_manifest.json after review.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dafan import toolchain  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT / "tools"))
    ap.add_argument("--skip-ffmpeg", action="store_true")
    ap.add_argument("--skip-esrgan", action="store_true")
    args = ap.parse_args()
    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    os.environ["DAFAN_TOOLS_DIR"] = str(dest)

    def bar(name):
        last = [-1]

        def cb(done, total):
            pct = int(done * 100 / total) if total else 0
            if pct // 10 != last[0]:
                last[0] = pct // 10
                print(f"  {name}: {done / 1e6:.1f} / {total / 1e6:.1f} MB ({pct}%)", flush=True)
        return cb

    try:
        if not args.skip_esrgan:
            print("Downloading Real-ESRGAN ...", flush=True)
            toolchain.install_realesrgan(bar("realesrgan"), dest_root=dest)
        if not args.skip_ffmpeg:
            print("Downloading FFmpeg ...", flush=True)
            toolchain.install_ffmpeg(bar("ffmpeg"), dest_root=dest)
    except Exception as exc:
        print(f"::error::download failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
