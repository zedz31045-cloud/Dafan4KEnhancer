"""Headless self-test used by the build pipeline (python app.py --selftest)."""
from __future__ import annotations

import json
import sys


def run(out_path: str | None, gui: bool = False) -> int:
    report: dict = {"ok": True, "checks": {}}

    def check(name: str, fn) -> None:
        try:
            report["checks"][name] = str(fn() or "ok")
        except Exception as exc:  # report every failure, do not stop at the first
            report["ok"] = False
            report["checks"][name] = f"FAILED: {type(exc).__name__}: {exc}"

    def imp(mod: str):
        return lambda: getattr(__import__(mod), "__version__", "ok")

    for mod in ("PIL", "numpy", "cv2", "customtkinter"):
        check(f"import {mod}", imp(mod))
    from . import __version__, toolchain
    from .i18n import TEXT
    check("version", lambda: __version__)
    check("strings", lambda: f"{len(TEXT)} keys")
    check("manifest", lambda: sorted(toolchain.load_manifest()))
    tc = toolchain.locate()
    problems = toolchain.verify_installed(tc)
    report["checks"]["tools"] = "all present" if not problems else "; ".join(problems)
    if problems:
        report["ok"] = False
    if gui:
        def make_window():
            import customtkinter as ctk
            root = ctk.CTk()
            root.update()
            root.destroy()
            return "window created"
        check("gui", make_window)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    return 0 if report["ok"] else 1
