"""Assemble the release folder + ZIP from PyInstaller's dist output.

Layout of the ZIP:
  Dafan4KEnhancer/
    Dafan4KEnhancer.exe  (+ _internal/ runtime)
    tools/realesrgan/... models/    <- downloaded at build time, not stored in git
    tools/ffmpeg/...
    README.md, THIRD_PARTY_NOTICES.md, LICENSE-NOTES.txt
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dafan import __version__  # noqa: E402


def main() -> int:
    dist = ROOT / "dist" / "Dafan4KEnhancer"
    if not dist.is_dir():
        print(f"::error::{dist} not found - did PyInstaller run?")
        return 1
    tools = ROOT / "tools"
    for need in ("realesrgan", "ffmpeg"):
        if not (tools / need).is_dir():
            print(f"::error::tools/{need} is missing")
            return 1
    target = dist / "tools"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(tools, target, ignore=shutil.ignore_patterns("_downloads", "*.part"))
    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, dist / name)
    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    zip_path = out / f"Dafan4KEnhancer-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(dist.rglob("*")):
            if f.is_file():
                zf.write(f, Path("Dafan4KEnhancer") / f.relative_to(dist))
    print(f"created {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
