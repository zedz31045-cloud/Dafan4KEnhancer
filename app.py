"""Dafan 4K Enhancer - entry point.

Run from source:   python app.py
Headless check:    python app.py --selftest [--selftest-out result.json] [--selftest-gui]
"""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dafan.ui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
