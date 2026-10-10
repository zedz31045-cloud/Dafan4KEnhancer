"""Fail loudly if a tool or model required by the app is missing or does not start."""
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
    os.environ["DAFAN_TOOLS_DIR"] = str(Path(args.dest).resolve())
    tc = toolchain.locate()
    problems = toolchain.verify_installed(tc)
    if args.skip_ffmpeg:
        problems = [p for p in problems if "ff" not in p]
    if args.skip_esrgan:
        problems = [p for p in problems if "realesrgan" not in p and "model" not in p]
    if problems:
        for p in problems:
            print(f"::error::{p}")
        return 1
    print("tools OK:", tc.realesrgan, tc.ffmpeg, tc.ffprobe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
