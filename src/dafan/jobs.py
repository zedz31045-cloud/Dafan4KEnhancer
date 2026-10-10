"""Job model and the thread-based queue manager.

All heavy work runs on worker threads. The UI never touches those threads: it only
reads Job objects and receives small notification tuples through ``events``
(a thread-safe ``queue.Queue``) that it drains from Tk's ``after`` loop.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import engine_image, engine_video, media, toolchain
from .config import Settings
from .history import History
from .i18n import t
from .paths import default_temp_dir
from .util import Cancelled, ProcessingError

log = logging.getLogger("dafan")


class Status:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    path: Path
    kind: str
    size: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    width: int = 0
    height: int = 0
    fmt: str = ""
    duration: float = 0.0
    video_info: media.VideoInfo | None = None
    probe_error: str = ""
    status: str = Status.PENDING
    progress: float = 0.0
    message: str = ""
    output: Path | None = None
    out_w: int = 0
    out_h: int = 0
    started: float | None = None
    elapsed: float = 0.0
    eta: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    settings: Settings | None = None

    @property
    def name(self) -> str:
        return self.path.name


def resolve_output_dir(settings: Settings, src: Path) -> Path:
    return Path(settings.output_dir) if settings.output_dir else src.parent / "Dafan_Output"


class QueueManager:
    def __init__(self, settings: Settings, history: History | None = None):
        self.settings = settings
        self.history = history or History()
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.jobs: list[Job] = []
        self._lock = threading.RLock()
        self._active = False
        self._threads: list[threading.Thread] = []
        self._probe_q: "queue.Queue[Job]" = queue.Queue()
        self._prober = threading.Thread(target=self._probe_loop, daemon=True, name="dafan-probe")
        self._prober.start()

    # ------------------------------------------------------------------ adding
    def add_paths(self, paths: list[str | Path]) -> tuple[list[Job], list[tuple[Path, str]]]:
        """Add files. Returns (added jobs, [(path, reason_key)] for rejected ones)."""
        added: list[Job] = []
        skipped: list[tuple[Path, str]] = []
        with self._lock:
            busy = {j.path.resolve() for j in self.jobs
                    if j.status in (Status.PENDING, Status.RUNNING)}
            for raw in paths:
                p = Path(raw)
                kind = media.kind_of(p)
                if kind is None:
                    skipped.append((p, "skip_type"))
                    continue
                try:
                    resolved = p.resolve()
                    if not resolved.is_file():
                        skipped.append((p, "skip_missing"))
                        continue
                    size = resolved.stat().st_size
                except OSError:
                    skipped.append((p, "skip_missing"))
                    continue
                if resolved in busy:
                    skipped.append((p, "skip_duplicate"))
                    continue
                busy.add(resolved)
                job = Job(path=resolved, kind=kind, size=size)
                self.jobs.append(job)
                added.append(job)
                self._probe_q.put(job)
        self.events.put(("jobs",))
        return added, skipped

    def _probe_loop(self) -> None:
        while True:
            job = self._probe_q.get()
            try:
                if job.kind == "image":
                    job.width, job.height, job.fmt = media.probe_image(job.path)
                else:
                    tc = toolchain.locate()
                    if tc.ffprobe:
                        info = media.probe_video(job.path, tc.ffprobe)
                        job.video_info = info
                        job.width, job.height, job.duration = info.width, info.height, info.duration
                        job.fmt = info.vcodec.upper()
            except Exception as exc:
                job.probe_error = str(exc)[:200]
            self.events.put(("job", job.id))

    # ------------------------------------------------------------------ control
    def start_all(self, settings: Settings | None = None) -> int:
        """Start (or continue) processing pending jobs. Safe to call repeatedly."""
        with self._lock:
            if settings:
                self.settings = settings
            self._active = True
            self._threads = [th for th in self._threads if th.is_alive()]
            wanted = self.settings.max_concurrent - len(self._threads)
            pending = sum(1 for j in self.jobs if j.status == Status.PENDING)
            for _ in range(max(0, min(wanted, pending))):
                th = threading.Thread(target=self._worker, daemon=True, name="dafan-worker")
                self._threads.append(th)
                th.start()
            return pending

    def stop_all(self) -> None:
        """Stop everything: running jobs are cancelled, pending jobs stay queued."""
        with self._lock:
            self._active = False
            for j in self.jobs:
                if j.status == Status.RUNNING:
                    j.cancel.set()

    def cancel_current(self) -> None:
        """Cancel the running job(s) only; the queue keeps going."""
        with self._lock:
            for j in self.jobs:
                if j.status == Status.RUNNING:
                    j.cancel.set()

    def cancel(self, job_id: str) -> None:
        with self._lock:
            j = self._find(job_id)
            if not j:
                return
            if j.status == Status.RUNNING:
                j.cancel.set()
            elif j.status == Status.PENDING:
                j.status, j.message = Status.CANCELLED, t("msg_cancelled")
        self.events.put(("job", job_id))

    def retry(self, job_id: str) -> bool:
        with self._lock:
            j = self._find(job_id)
            if not j or j.status not in (Status.FAILED, Status.CANCELLED):
                return False
            self._reset(j)
        self.events.put(("job", job_id))
        return True

    def retry_failed(self) -> int:
        with self._lock:
            failed = [j for j in self.jobs if j.status == Status.FAILED]
            for j in failed:
                self._reset(j)
        self.events.put(("jobs",))
        return len(failed)

    def remove(self, job_id: str) -> bool:
        with self._lock:
            j = self._find(job_id)
            if not j or j.status == Status.RUNNING:
                return False
            self.jobs.remove(j)
        self.events.put(("jobs",))
        return True

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.status in (Status.PENDING, Status.RUNNING)]
        self.events.put(("jobs",))

    def stats(self) -> dict[str, int]:
        with self._lock:
            c = {s: 0 for s in (Status.PENDING, Status.RUNNING, Status.DONE, Status.FAILED, Status.CANCELLED)}
            for j in self.jobs:
                c[j.status] += 1
            return c

    def has_running(self) -> bool:
        with self._lock:
            return any(j.status == Status.RUNNING for j in self.jobs)

    def _find(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    @staticmethod
    def _reset(j: Job) -> None:
        j.status, j.progress, j.message, j.eta, j.elapsed = Status.PENDING, 0.0, "", None, 0.0
        j.cancel = threading.Event()

    # ------------------------------------------------------------------ expectations
    def summary(self, settings: Settings) -> tuple[int, int, int]:
        """(files waiting, estimated output bytes, files whose size could not be estimated)."""
        files = est = unknown = 0
        with self._lock:
            pend = [j for j in self.jobs if j.status == Status.PENDING]
        for j in pend:
            files += 1
            if not j.width:
                unknown += 1
                continue
            if j.kind == "image":
                sc = settings.image_scale
                est += engine_image.estimate_output_bytes(j.width, j.height, sc, settings.image_format,
                                                         j.path.suffix)
            elif j.video_info:
                try:
                    p = engine_video.VideoParams.from_settings(settings)
                    plan = engine_video.make_plan(j.video_info, p)
                    est += engine_video.estimate_video_bytes(j.video_info, plan, p)
                except ProcessingError:
                    unknown += 1
            else:
                unknown += 1
        return files, est, unknown

    # ------------------------------------------------------------------ workers
    def _worker(self) -> None:
        while True:
            job = self._next_job()
            if job is None:
                break
            self._execute(job)
        with self._lock:
            if not any(j.status == Status.RUNNING for j in self.jobs):
                self.events.put(("idle",))

    def _next_job(self) -> Job | None:
        with self._lock:
            if not self._active:
                return None
            for j in self.jobs:
                if j.status == Status.PENDING:
                    j.status = Status.RUNNING  # claimed under the lock: no job runs twice
                    j.started = time.time()
                    j.progress, j.message, j.eta = 0.0, "", None
                    j.settings = self.settings.copy()
                    return j
        return None

    def _execute(self, job: Job) -> None:
        self.events.put(("job", job.id))
        s = job.settings or self.settings
        last_emit = [0.0]

        def progress(frac: float) -> None:
            job.progress = max(0.0, min(1.0, frac))
            job.elapsed = time.time() - (job.started or time.time())
            job.eta = job.elapsed * (1 - job.progress) / job.progress if job.progress > 0.02 else None
            now = time.time()
            if now - last_emit[0] > 0.25:
                last_emit[0] = now
                self.events.put(("job", job.id))

        try:
            if not job.path.is_file():
                raise ProcessingError(t("skip_missing"))
            out_dir = resolve_output_dir(s, job.path)
            if not media.is_writable_dir(out_dir):
                raise ProcessingError(t("err_output_dir", path=str(out_dir)))
            temp_root = Path(s.temp_dir) if s.temp_dir else default_temp_dir()
            if not media.is_writable_dir(temp_root):
                raise ProcessingError(t("err_output_dir", path=str(temp_root)))
            tc = toolchain.locate()
            if job.kind == "image":
                params = engine_image.ImageParams.from_settings(s)
                miss = toolchain.missing_tools("image", tc, params.models_needed())
                if miss:
                    raise ProcessingError(t("err_missing_list", items=", ".join(miss)))
                job.out_w, job.out_h = job.width * s.image_scale, job.height * s.image_scale
                out = engine_image.enhance_image(job.path, out_dir, params, tc, job.cancel, progress, temp_root)
            else:
                if not (tc.ffmpeg and tc.ffprobe):
                    raise ProcessingError(t("err_missing_list", items="ffmpeg"))
                info = job.video_info or media.probe_video(job.path, tc.ffprobe)
                params = engine_video.VideoParams.from_settings(s)
                miss = toolchain.missing_tools("video", tc, engine_video.models_needed(info, params))
                if miss:
                    raise ProcessingError(t("err_missing_list", items=", ".join(miss)))
                plan = engine_video.make_plan(info, params)
                job.out_w, job.out_h = plan.out_w, plan.out_h
                out = engine_video.enhance_video(job.path, out_dir, info, params, tc, job.cancel,
                                                 progress, temp_root)
            job.output = out
            job.status, job.progress, job.message = Status.DONE, 1.0, t("msg_done")
        except Cancelled:
            job.status, job.message = Status.CANCELLED, t("msg_cancelled")
        except ProcessingError as exc:
            job.status, job.message = Status.FAILED, str(exc)
        except Exception as exc:  # unexpected: keep the app alive, keep the traceback in the log
            log.error("job failed\n%s", traceback.format_exc())
            job.status, job.message = Status.FAILED, t("err_unexpected", err=f"{type(exc).__name__}: {exc}")
        job.elapsed = time.time() - (job.started or time.time())
        job.eta = None
        self.history.add(file=str(job.path), kind=job.kind, status=job.status,
                         output=str(job.output) if job.output else "", message=job.message,
                         seconds=round(job.elapsed, 1))
        self.events.put(("job", job.id))
        self.events.put(("stats",))
