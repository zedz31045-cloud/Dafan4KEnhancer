"""Process helpers: cancellable subprocess runs, log tails, friendly errors."""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, Sequence

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Cancelled(Exception):
    """Raised when the user cancels a running job."""


class ProcessingError(Exception):
    """A failure with a message that is safe to show to the user."""


def popen(cmd: Sequence[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen([str(c) for c in cmd], creationflags=CREATE_NO_WINDOW, **kwargs)


def run_quiet(cmd: Sequence[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(c) for c in cmd], capture_output=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_cancellable(
    cmd: Sequence[str],
    cancel: threading.Event,
    log_path: Path,
    on_tick: Callable[[], None] | None = None,
    tick: float = 0.3,
) -> int:
    """Run a command, logging stdout+stderr to a file, polling for cancel.

    The heavy work never blocks the caller's ability to react: every `tick`
    seconds we check the cancel flag and call `on_tick` (used for progress).
    """
    with open(log_path, "ab") as log:
        proc = popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        try:
            while True:
                try:
                    rc = proc.wait(timeout=tick)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if cancel.is_set():
                    kill_tree(proc)
                    proc.wait()
                    raise Cancelled()
                if on_tick:
                    on_tick()
        except BaseException:
            kill_tree(proc)
            raise
    if on_tick:
        on_tick()
    return rc


def tail(path: Path, nbytes: int = 3000) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def last_percent(text: str) -> float | None:
    """Last 'NN.NN%' value in a text blob (Real-ESRGAN prints these per tile)."""
    found = re.findall(r"(\d{1,3}(?:\.\d+)?)%", text)
    if not found:
        return None
    try:
        return min(100.0, float(found[-1]))
    except ValueError:
        return None


def friendly_failure(log_text: str) -> str:
    """Map known tool failure patterns to a short hint key (see i18n)."""
    low = log_text.lower()
    if any(p in low for p in ("vkallocatememory", "out of memory", "vk_error_out_of", "bad_alloc",
                              "failed to allocate", "cannot allocate")):
        return "err_memory"
    if "no space left" in low or "not enough space" in low:
        return "err_disk"
    if "vulkan" in low and ("failed" in low or "no" in low and "device" in low):
        return "err_vulkan"
    return ""
