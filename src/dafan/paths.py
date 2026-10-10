"""Locations of resources, bundled tools and user data (works frozen or not)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_ID


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Folder that holds the program (next to the EXE, or the project root)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """Folder with read-only bundled resources (assets, manifest)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parents[2]


def tools_dir() -> Path:
    override = os.environ.get("DAFAN_TOOLS_DIR")
    return Path(override) if override else app_dir() / "tools"


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") if os.name == "nt" else None
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_temp_dir() -> Path:
    path = user_data_dir() / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path
