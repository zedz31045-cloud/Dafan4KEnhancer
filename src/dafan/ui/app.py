"""Main window: sidebar navigation, queue page, event loop wiring."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageOps
from tkinter import filedialog, messagebox

from .. import APP_NAME, __version__, engine_image, engine_video, i18n, media, toolchain
from ..config import load_settings
from ..i18n import t
from ..jobs import QueueManager, Status, resolve_output_dir
from ..media import human_size
from ..paths import app_dir, default_temp_dir, resource_dir, tools_dir
from ..util import Cancelled
from . import theme as th
from .compare import CompareWindow
from .pages import PagesMixin
from .widgets import JobRow, StatCard

try:  # optional: native drag & drop
    from tkinterdnd2 import DND_FILES, TkinterDnD

    class _Base(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)

    HAS_DND = True
except Exception:  # missing package or missing tkdnd binaries: the Add buttons still work
    DND_FILES = None

    class _Base(ctk.CTk):
        pass

    HAS_DND = False

NAV = [("queue", "nav_queue", "≡"), ("image", "nav_image", "▣"), ("video", "nav_video", "▶"),
       ("history", "nav_history", "⟲"), ("settings", "nav_settings", "⚙"),
       ("about", "nav_about", "ⓘ")]
FILETYPES = "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.mp4 *.mov *.mkv *.avi *.webm"


class App(PagesMixin, _Base):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        i18n.set_language(self.settings.language)
        self.manager = QueueManager(self.settings)
        self.ui_q: "queue.Queue[tuple]" = queue.Queue()
        self.pages: dict = {}
        self.rows: dict[str, JobRow] = {}
        self.nav: dict = {}
        self.gpus: list[str] = []
        self.selected: str | None = None
        self.page_name = "queue"
        self.anchor, self.justify = "w", "left"
        self._save_job = None
        self._anim = None
        self._toast_widget = None
        self._preview_img = None
        self._downloading = False
        self._start_after_dl = False
        self._dl_cancel = threading.Event()
        self._was_running = False
        self.tc = toolchain.locate()

        ctk.set_appearance_mode("dark")
        self.title(f"{APP_NAME}  {__version__}")
        self.geometry("1320x820")
        self.minsize(1040, 660)
        self.configure(fg_color=th.BG)
        self.after(250, self._set_icon)
        self._build()
        self._setup_dnd()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(700, self._startup_check)

    # ------------------------------------------------------------------ shell
    def _set_icon(self) -> None:
        icon = resource_dir() / "assets" / "icon.ico"
        if icon.is_file() and os.name == "nt":
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

    def _build(self) -> None:
        rtl = i18n.current() == "ar"
        self.anchor, self.justify = ("e", "right") if rtl else ("w", "left")
        for attr in ("sidebar", "content"):
            if hasattr(self, attr):
                getattr(self, attr).destroy()
        self.pages, self.rows, self.nav = {}, {}, {}
        sb_col, ct_col = (1, 0) if rtl else (0, 1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(sb_col, weight=0)
        self.grid_columnconfigure(ct_col, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=232, fg_color=th.SIDEBAR, corner_radius=0)
        self.sidebar.grid(row=0, column=sb_col, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.content = ctk.CTkFrame(self, fg_color=th.BG, corner_radius=0)
        self.content.grid(row=0, column=ct_col, sticky="nsew")
        self._build_sidebar()
        self._build_queue_page()
        self._build_image_page()
        self._build_video_page()
        self._build_history_page()
        self._build_settings_page()
        self._build_about_page()
        self._show_gpu_label()
        self.show_page(self.page_name, animate=False)
        self._sync_rows()
        self._refresh_stats()
        self._refresh_summary()

    def _build_sidebar(self) -> None:
        sb = self.sidebar
        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=18, pady=(26, 22))
        ctk.CTkLabel(logo, text="DAFAN", font=th.font(32, "bold"), text_color=th.ACCENT, anchor="w").pack(fill="x")
        row = ctk.CTkFrame(logo, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="4K", font=th.font(12, "bold"), text_color=th.BG, fg_color=th.ACCENT,
                     corner_radius=6, width=34, height=22).pack(side="left")
        ctk.CTkLabel(row, text="ENHANCER", font=th.font(14, "bold"), text_color=th.TEXT).pack(side="left", padx=8)
        for name, key, icon in NAV:
            btn = ctk.CTkButton(
                sb, text=f"  {icon}   {t(key)}", anchor=self.anchor, height=44, corner_radius=10,
                fg_color="transparent", hover_color=th.CARD2, text_color=th.MUTED, font=th.font(14, "bold"),
                command=lambda n=name: self.show_page(n))
            btn.pack(fill="x", padx=12, pady=3)
            self.nav[name] = btn
        ctk.CTkFrame(sb, fg_color="transparent").pack(expand=True, fill="both")
        self.tools_status = ctk.CTkLabel(sb, text="", font=th.font(11), text_color=th.MUTED,
                                         anchor="w", justify="left", wraplength=200)
        self.tools_status.pack(fill="x", padx=18, pady=(0, 8))
        other = "en" if i18n.current() == "ar" else "ar"
        ctk.CTkButton(sb, text="English" if other == "en" else "العربية", height=36,
                      corner_radius=10, fg_color=th.CARD2, hover_color=th.ACCENT_DARK, text_color=th.TEXT,
                      font=th.font(13, "bold"), command=lambda: self._set("language", other)
                      ).pack(fill="x", padx=14, pady=(0, 18))

    def _highlight_nav(self) -> None:
        for name, btn in self.nav.items():
            on = name == self.page_name
            btn.configure(fg_color=th.NAVY if on else "transparent", text_color=th.ACCENT if on else th.MUTED)

    def show_page(self, name: str, animate: bool = True) -> None:
        if self._anim is not None:
            self.after_cancel(self._anim)
            self._anim = None
        for n, f in self.pages.items():
            if n != name:
                f.place_forget()
        self.page_name = name
        self._highlight_nav()
        page = self.pages[name]
        if animate:
            self._slide_in(page, 0)
        else:
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        if name == "history":
            self._refresh_history()
        if name == "settings":
            self._refresh_tools()

    def _slide_in(self, page, step: int, steps: int = 9) -> None:
        remaining = (1 - step / steps) ** 2
        sign = -1 if i18n.current() == "ar" else 1
        page.place(relx=sign * 0.04 * remaining, rely=0, relwidth=1, relheight=1)
        if step < steps:
            self._anim = self.after(16, lambda: self._slide_in(page, step + 1, steps))
        else:
            self._anim = None
            page.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _toast(self, text: str, kind: str = "info") -> None:
        color = {"info": th.ACCENT, "ok": th.OK, "err": th.ERR, "warn": th.WARN}.get(kind, th.ACCENT)
        if self._toast_widget is not None:
            try:
                self._toast_widget.destroy()
            except Exception:
                pass
        frame = ctk.CTkFrame(self, fg_color=th.CARD2, corner_radius=12, border_width=2, border_color=color)
        ctk.CTkLabel(frame, text=text, font=th.font(13), text_color=th.TEXT, wraplength=720,
                     justify=self.justify).pack(padx=18, pady=11)
        frame.place(relx=0.5, rely=0.97, anchor="s")
        frame.lift()
        self._toast_widget = frame
        self.after(4500, lambda f=frame: f.destroy() if f.winfo_exists() else None)

    # ------------------------------------------------------------------ queue page
    def _build_queue_page(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color=th.BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(4, weight=1)
        self.pages["queue"] = page
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 6))
        ctk.CTkLabel(head, text=t("queue_title"), font=th.font(26, "bold"), text_color=th.TEXT,
                     anchor=self.anchor, justify=self.justify).pack(fill="x")
        ctk.CTkLabel(head, text=t("queue_sub"), font=th.font(13), text_color=th.MUTED, anchor=self.anchor,
                     justify=self.justify, wraplength=900).pack(fill="x")

        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=22, pady=6)
        self.cards = {}
        for i, (st, color) in enumerate(((Status.DONE, th.OK), (Status.FAILED, th.ERR),
                                         (Status.RUNNING, th.ACCENT), (Status.PENDING, th.MUTED))):
            stats.grid_columnconfigure(i, weight=1, uniform="stat")
            card = StatCard(stats, t("stat_" + st), color)
            card.grid(row=0, column=i, padx=6, sticky="ew")
            self.cards[st] = card

        self.drop = ctk.CTkFrame(page, fg_color=th.CARD, corner_radius=16, border_width=2,
                                 border_color=th.ACCENT_DARK)
        self.drop.grid(row=2, column=0, sticky="ew", padx=28, pady=8)
        self.drop.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.drop, text="⇩", font=th.font(30, "bold"), text_color=th.ACCENT).grid(
            row=0, column=0, pady=(10, 0))
        ctk.CTkLabel(self.drop, text=t("drop_hint") if HAS_DND else t("drop_hint_nodnd"), font=th.font(14),
                     text_color=th.TEXT).grid(row=1, column=0)
        btns = ctk.CTkFrame(self.drop, fg_color="transparent")
        btns.grid(row=2, column=0, pady=(6, 12))
        self._btn(btns, t("btn_add_files"), self._add_files).pack(side="left", padx=5)
        self._btn(btns, t("btn_add_folder"), self._add_folder, secondary=True).pack(side="left", padx=5)
        self.drop.bind("<Enter>", lambda _e: self.drop.configure(border_color=th.ACCENT))
        self.drop.bind("<Leave>", lambda _e: self.drop.configure(border_color=th.ACCENT_DARK))

        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=22, pady=(2, 6))
        for text, cmd, sec in ((t("btn_start_all"), self._start_all, False),
                               (t("btn_stop_all"), self._stop_all, True),
                               (t("btn_cancel_current"), self._cancel_current, True),
                               (t("btn_retry_failed"), self._retry_failed, True),
                               (t("btn_clear_finished"), self._clear_finished, True),
                               (t("btn_open_output"), self._open_output, True)):
            self._btn(bar, text, cmd, secondary=sec, small=True).pack(side="left", padx=5)

        main = ctk.CTkFrame(page, fg_color="transparent")
        main.grid(row=4, column=0, sticky="nsew", padx=22, pady=4)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.list_frame = ctk.CTkScrollableFrame(main, fg_color=th.PANEL, corner_radius=14, border_width=1,
                                                 border_color=th.BORDER)
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=(6, 8))
        self.empty_lbl = ctk.CTkLabel(self.list_frame, text=t("queue_empty"), font=th.font(14),
                                      text_color=th.MUTED, wraplength=520)

        side = ctk.CTkFrame(main, fg_color=th.PANEL, corner_radius=14, border_width=1, border_color=th.BORDER,
                            width=310)
        side.grid(row=0, column=1, sticky="ns", padx=(0, 6))
        side.grid_propagate(False)
        ctk.CTkLabel(side, text=t("preview_title"), font=th.font(15, "bold"), text_color=th.ACCENT).pack(
            padx=14, pady=(12, 6), anchor="w")
        self.preview_lbl = ctk.CTkLabel(side, text=t("preview_none"), width=280, height=200, fg_color=th.BG,
                                        corner_radius=10, text_color=th.MUTED, font=th.font(12))
        self.preview_lbl.pack(padx=14, pady=4)
        self.preview_info = ctk.CTkLabel(side, text="", font=th.font(12), text_color=th.TEXT, wraplength=275,
                                         justify=self.justify, anchor=self.anchor)
        self.preview_info.pack(fill="x", padx=14, pady=8)
        self._btn(side, t("btn_compare"), self._compare_selected).pack(padx=14, pady=4, fill="x")

        foot = ctk.CTkFrame(page, fg_color=th.PANEL, corner_radius=12, border_width=1, border_color=th.BORDER)
        foot.grid(row=5, column=0, sticky="ew", padx=28, pady=(6, 16))
        foot.grid_columnconfigure(0, weight=1)
        self.total_bar = ctk.CTkProgressBar(foot, height=10, progress_color=th.ACCENT, fg_color=th.NAVY)
        self.total_bar.set(0)
        self.total_bar.grid(row=0, column=0, sticky="ew", padx=(16, 10), pady=(12, 4))
        self.total_lbl = ctk.CTkLabel(foot, text="", font=th.font(12, "bold"), text_color=th.ACCENT, width=110)
        self.total_lbl.grid(row=0, column=1, padx=(0, 16), pady=(12, 4))
        self.est_lbl = ctk.CTkLabel(foot, text="", font=th.font(12), text_color=th.MUTED, anchor=self.anchor,
                                    justify=self.justify, wraplength=900)
        self.est_lbl.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

    # ------------------------------------------------------------------ adding files
    def _setup_dnd(self) -> None:
        if not HAS_DND:
            return
        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event) -> None:
        try:
            paths = list(self.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        self.show_page("queue")
        self._add_paths(paths)

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(parent=self, title=t("dlg_add_files"),
                                            filetypes=[(t("dlg_media"), FILETYPES), (t("dlg_all"), "*.*")])
        if files:
            self._add_paths(list(files))

    def _add_folder(self) -> None:
        folder = self._ask_folder()
        if folder:
            self._add_paths([folder])

    def _ask_folder(self) -> str:
        return filedialog.askdirectory(parent=self) or ""

    def _add_paths(self, paths: list) -> None:
        files = media.collect_media_files(paths)
        if not files:
            self._toast(t("no_media_found"), "warn")
            return
        added, skipped = self.manager.add_paths(files)
        msg = t("added_n", n=len(added))
        if skipped:
            reasons = {r for _, r in skipped}
            msg += "  •  " + t("skipped_n", n=len(skipped)) + " (" + ", ".join(t(r) for r in sorted(reasons)) + ")"
        self._toast(msg, "ok" if added else "warn")

    # ------------------------------------------------------------------ queue actions
    def _start_all(self) -> None:
        pending = [j for j in self.manager.jobs if j.status == Status.PENDING]
        if not pending:
            self._toast(t("nothing_to_do"), "warn")
            return
        self.tc = toolchain.locate()
        missing = self._preflight(pending)
        if missing:
            if messagebox.askyesno(t("dl_title"), t("dl_confirm", items=", ".join(missing)), parent=self):
                self.show_page("settings")
                self._download_tools(start_after=True)
            return
        n = self.manager.start_all(self.settings)
        self._toast(t("started_n", n=n), "ok")

    def _preflight(self, pending) -> list[str]:
        missing: set[str] = set()
        for j in pending:
            if j.kind == "image":
                p = engine_image.ImageParams.from_settings(self.settings)
                missing.update(toolchain.missing_tools("image", self.tc, p.models_needed()))
            else:
                vp = engine_video.VideoParams.from_settings(self.settings)
                models = engine_video.models_needed(j.video_info, vp) if j.video_info else []
                missing.update(toolchain.missing_tools("video", self.tc, models))
        return sorted(missing)

    def _stop_all(self) -> None:
        self.manager.stop_all()
        self._toast(t("stopping"), "warn")

    def _cancel_current(self) -> None:
        if not self.manager.has_running():
            self._toast(t("nothing_running"), "warn")
            return
        self.manager.cancel_current()

    def _retry_failed(self) -> None:
        if self.manager.retry_failed():
            self._start_all()
        else:
            self._toast(t("no_failed"), "warn")

    def _clear_finished(self) -> None:
        self.manager.clear_finished()
        self.selected = None

    def _row_action(self, action: str, job_id: str) -> None:
        if action == "preview":
            self._select(job_id)
        elif action == "compare":
            self._select(job_id)
            self._compare_selected()
        elif action == "retry":
            if self.manager.retry(job_id):
                self._start_all()
        elif action == "cancel":
            self.manager.cancel(job_id)
        elif action == "remove":
            self.manager.remove(job_id)

    def _job(self, job_id: str | None):
        return next((j for j in self.manager.jobs if j.id == job_id), None)

    def _open_output(self) -> None:
        job = self._job(self.selected)
        folder: Path | None = None
        if job and job.output:
            folder = job.output.parent
        else:
            done = [j for j in self.manager.jobs if j.output]
            if done:
                folder = done[-1].output.parent
            elif self.settings.output_dir:
                folder = Path(self.settings.output_dir)
            elif self.manager.jobs:
                folder = resolve_output_dir(self.settings, self.manager.jobs[0].path)
        if folder is None or not folder.exists():
            self._toast(t("no_output_yet"), "warn")
            return
        self._open_path(folder)

    def _open_path(self, path) -> None:
        path = Path(path)
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as exc:
            self._toast(t("err_open", err=str(exc)), "err")

    # ------------------------------------------------------------------ preview / compare
    def _select(self, job_id: str) -> None:
        self.selected = job_id
        for jid, row in self.rows.items():
            row.select(jid == job_id)
        job = self._job(job_id)
        if not job:
            return
        self._refresh_preview_info(job)
        self.preview_lbl.configure(image=None, text=t("preview_loading"))
        threading.Thread(target=self._preview_worker, args=(job,), daemon=True).start()

    def _refresh_preview_info(self, job) -> None:
        lines = [job.name, f"{human_size(job.size)}  •  {job.width}×{job.height}" if job.width else human_size(job.size)]
        if job.output:
            lines.append(t("out_file") + ": " + job.output.name)
            lines.append(f"{job.out_w}×{job.out_h}")
        self.preview_info.configure(text="\n".join(lines))

    def _preview_worker(self, job) -> None:
        try:
            if job.kind == "image":
                with Image.open(job.path) as im:
                    if im.format == "JPEG":
                        im.draft("RGB", (1200, 1200))
                    img = ImageOps.exif_transpose(im)
                    img.thumbnail((560, 400))
                    img = img.convert("RGB")
            else:
                tc = toolchain.locate()
                if not tc.ffmpeg:
                    raise RuntimeError("FFmpeg")
                tmp = default_temp_dir() / f"prev_{job.id}.png"
                try:
                    media.extract_frame(tc.ffmpeg, job.path, min(1.0, (job.duration or 2) / 2), tmp)
                    with Image.open(tmp) as im:
                        im.load()
                        img = im.convert("RGB")
                finally:
                    tmp.unlink(missing_ok=True)
                img.thumbnail((560, 400))
            self.ui_q.put(("preview", job.id, img))
        except Exception as exc:
            self.ui_q.put(("preview_err", job.id, str(exc)))

    def _compare_selected(self) -> None:
        job = self._job(self.selected)
        if not job or job.status != Status.DONE or not job.output:
            self._toast(t("compare_need_done"), "warn")
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            if job.kind == "image":
                with Image.open(job.path) as a:
                    before = ImageOps.exif_transpose(a).convert("RGB")
                with Image.open(job.output) as b:
                    after = b.convert("RGB")
            else:
                tc = toolchain.locate()
                ts = min(1.0, (job.duration or 2) / 2)
                tmp = default_temp_dir()
                p1, p2 = tmp / f"cmp_a_{job.id}.png", tmp / f"cmp_b_{job.id}.png"
                try:
                    media.extract_frame(tc.ffmpeg, job.path, ts, p1)
                    media.extract_frame(tc.ffmpeg, job.output, ts, p2)
                    with Image.open(p1) as a:
                        before = a.convert("RGB")
                    with Image.open(p2) as b:
                        after = b.convert("RGB")
                finally:
                    p1.unlink(missing_ok=True)
                    p2.unlink(missing_ok=True)
        except Exception as exc:
            self._toast(t("err_compare", err=str(exc)), "err")
            return
        finally:
            self.configure(cursor="")
        info = f"{job.name}   {before.width}×{before.height}  →  {after.width}×{after.height}"
        win = CompareWindow(self, before, after, t("compare_title"), info)
        win.after(100, win.lift)

    # ------------------------------------------------------------------ refreshing
    def _sync_rows(self) -> None:
        jobs = self.manager.jobs
        ids = {j.id for j in jobs}
        for jid in list(self.rows):
            if jid not in ids:
                self.rows.pop(jid).destroy()
                if self.selected == jid:
                    self.selected = None
                    self.preview_lbl.configure(image=None, text=t("preview_none"))
                    self.preview_info.configure(text="")
        for j in jobs:
            row = self.rows.get(j.id)
            if row is None:
                row = JobRow(self.list_frame, j, self._select, self._row_action)
                row.pack(fill="x", padx=6, pady=4)
                self.rows[j.id] = row
            else:
                row.update_view()
        if self.rows:
            self.empty_lbl.pack_forget()
        else:
            self.empty_lbl.pack(pady=60)

    def _refresh_stats(self) -> None:
        stats = self.manager.stats()
        for st, card in self.cards.items():
            card.set(stats.get(st, 0))
        active = [j for j in self.manager.jobs if j.status in (Status.PENDING, Status.RUNNING, Status.DONE)]
        frac = sum(j.progress for j in active) / len(active) if active else 0.0
        self.total_bar.set(frac)
        done = stats.get(Status.DONE, 0)
        self.total_lbl.configure(text=f"{done}/{len(active)}  ({int(frac * 100)}%)" if active else "")

    def _refresh_summary(self) -> None:
        if not hasattr(self, "est_lbl"):
            return
        files, est, unknown = self.manager.summary(self.settings)
        color = th.MUTED
        if files == 0:
            text = t("est_none")
        else:
            text = t("est_line", files=files, size=human_size(est))
            if unknown:
                text += "  " + t("est_unknown", n=unknown)
            pend = next((j for j in self.manager.jobs if j.status == Status.PENDING), None)
            if pend:
                try:
                    free = media.free_bytes(resolve_output_dir(self.settings, pend.path))
                    text += "  •  " + t("free_space", size=human_size(free))
                    if est > free:
                        color = th.WARN
                        text += "  ⚠ " + t("not_enough_space")
                except OSError:
                    pass
        self.est_lbl.configure(text=text, text_color=color)

    # ------------------------------------------------------------------ tools / GPU / downloads
    def _tools_folder(self) -> Path:
        folder = tools_dir()
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _refresh_tools(self) -> None:
        self.tc = toolchain.locate()
        tc = self.tc
        if not hasattr(self, "tool_lbls") or not self.tool_lbls:
            return
        ok, bad = th.OK, th.ERR
        self.tool_lbls["esrgan"].configure(text=t("found") if tc.realesrgan else t("missing"),
                                           text_color=ok if tc.realesrgan else bad)
        ff_ok = bool(tc.ffmpeg and tc.ffprobe)
        self.tool_lbls["ffmpeg"].configure(text=t("found") if ff_ok else t("missing"), text_color=ok if ff_ok else bad)
        models_ok = bool(tc.realesrgan) and all(toolchain.model_installed(tc, m, s) for m, s in
                                                (("realesrgan-x4plus", 4), ("realesrgan-x4plus-anime", 4),
                                                 ("realesr-animevideov3", 2), ("realesr-animevideov3", 4)))
        self.tool_lbls["models"].configure(text=t("found") if models_ok else t("missing"),
                                           text_color=ok if models_ok else bad)
        self.tools_status.configure(
            text=("● " + t("tools_ready")) if (tc.realesrgan and ff_ok and models_ok) else ("● " + t("tools_missing")),
            text_color=ok if (tc.realesrgan and ff_ok and models_ok) else th.WARN)

    def _startup_check(self) -> None:
        self._refresh_tools()
        if not (self.tc.realesrgan and self.tc.ffmpeg and self.tc.ffprobe):
            self._toast(t("tools_first_run"), "warn")

    def _detect_gpu(self) -> None:
        if not self.tc.realesrgan:
            self._toast(t("err_tool_missing", tool="Real-ESRGAN"), "err")
            return
        self.gpu_lbl.configure(text=t("detecting"))
        threading.Thread(target=lambda: self.ui_q.put(("gpus", toolchain.list_gpus(self.tc))), daemon=True).start()

    def _show_gpu_label(self) -> None:
        if hasattr(self, "gpu_lbl"):
            self.gpu_lbl.configure(text=(", ".join(self.gpus) if self.gpus else ""))

    def _download_tools(self, start_after: bool = False) -> None:
        if self._downloading:
            return
        tc = toolchain.locate()
        need_esrgan = (not tc.realesrgan) or not all(
            toolchain.model_installed(tc, m, s) for m, s in (("realesrgan-x4plus", 4), ("realesr-animevideov3", 2)))
        need_ffmpeg = not (tc.ffmpeg and tc.ffprobe)
        if not (need_esrgan or need_ffmpeg):
            self._toast(t("tools_ok"), "ok")
            if start_after:
                self._start_all()
            return
        if not start_after and not messagebox.askyesno(t("dl_title"), t("dl_confirm_sizes"), parent=self):
            return
        self._downloading, self._start_after_dl = True, start_after
        self._dl_cancel = threading.Event()
        self.dl_btn.configure(state="disabled")
        self.dl_cancel_btn.pack(side="left", padx=4)
        self.dl_bar.grid()
        self.dl_bar.set(0)

        def work() -> None:
            err = ""
            try:
                if need_esrgan:
                    toolchain.install_realesrgan(lambda d, tot: self.ui_q.put(("dl", "Real-ESRGAN", d, tot)),
                                                 self._dl_cancel)
                if need_ffmpeg:
                    toolchain.install_ffmpeg(lambda d, tot: self.ui_q.put(("dl", "FFmpeg", d, tot)), self._dl_cancel)
                problems = toolchain.verify_installed(toolchain.locate())
                if problems:
                    err = "; ".join(problems)
            except Cancelled:
                err = t("msg_cancelled")
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
            self.ui_q.put(("dl_done", err))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ event loop
    def _poll(self) -> None:
        jobs_changed, stats_changed, touched, idle = False, False, set(), False
        try:
            while True:
                ev = self.manager.events.get_nowait()
                if ev[0] == "jobs":
                    jobs_changed = True
                elif ev[0] == "job":
                    touched.add(ev[1])
                    stats_changed = True
                elif ev[0] == "stats":
                    stats_changed = True
                elif ev[0] == "idle":
                    idle = True
        except queue.Empty:
            pass
        if jobs_changed:
            self._sync_rows()
        for jid in touched:
            row = self.rows.get(jid)
            if row:
                row.update_view()
                if jid == self.selected and row.job.status == Status.DONE:
                    self._refresh_preview_info(row.job)
        if jobs_changed or stats_changed or idle:
            self._refresh_stats()
            self._refresh_summary()
        if idle:
            st = self.manager.stats()
            if st.get(Status.DONE) or st.get(Status.FAILED):
                self._toast(t("all_finished", ok=st.get(Status.DONE, 0), bad=st.get(Status.FAILED, 0)),
                            "ok" if not st.get(Status.FAILED) else "warn")
        try:
            while True:
                self._handle_ui(self.ui_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle_ui(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "preview":
            if ev[1] == self.selected:
                img = ev[2]
                self._preview_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self.preview_lbl.configure(image=self._preview_img, text="", width=img.width, height=img.height)
        elif kind == "preview_err":
            if ev[1] == self.selected:
                self.preview_lbl.configure(image=None, text=t("preview_failed"))
        elif kind == "gpus":
            self.gpus = ev[1]
            self.gpu_lbl.configure(text=", ".join(self.gpus) if self.gpus else t("no_gpu"))
            self._build()
            self.show_page("settings", animate=False)
        elif kind == "dl":
            _, name, done, total = ev
            if total:
                self.dl_bar.set(done / total)
            self.dl_lbl.configure(text=t("dl_progress", name=name, done=human_size(done),
                                         total=human_size(total) if total else "?"))
        elif kind == "dl_done":
            err = ev[1]
            self._downloading = False
            self.dl_btn.configure(state="normal")
            self.dl_cancel_btn.pack_forget()
            self.dl_bar.grid_remove()
            self._refresh_tools()
            if err:
                self.dl_lbl.configure(text=t("dl_failed", err=err), text_color=th.ERR)
                self._toast(t("dl_failed", err=err), "err")
            else:
                self.dl_lbl.configure(text=t("dl_ok"), text_color=th.OK)
                self._toast(t("dl_ok"), "ok")
                if self._start_after_dl:
                    self._start_all()
            self._start_after_dl = False

    # ------------------------------------------------------------------ language / closing
    def _switch_language(self) -> None:
        i18n.set_language(self.settings.language)
        self._build()

    def _on_close(self) -> None:
        if self.manager.has_running():
            if not messagebox.askyesno(APP_NAME, t("confirm_exit"), parent=self):
                return
        self.manager.stop_all()
        if self._save_job is not None:
            self.after_cancel(self._save_job)
            self._save_now()
        self._wait_close(30)

    def _wait_close(self, tries: int) -> None:
        if self.manager.has_running() and tries > 0:
            self.after(200, lambda: self._wait_close(tries - 1))
        else:
            self.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        from ..selftest import run
        out = argv[argv.index("--selftest-out") + 1] if "--selftest-out" in argv else None
        return run(out, gui="--selftest-gui" in argv)
    app = App()
    app.mainloop()
    return 0
