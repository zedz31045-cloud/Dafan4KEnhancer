"""Reusable widgets: statistic cards and queue rows."""
from __future__ import annotations

import customtkinter as ctk

from ..i18n import t
from ..jobs import Job, Status
from ..media import human_size, human_time
from . import theme as th


class StatCard(ctk.CTkFrame):
    def __init__(self, master, title: str, color: str):
        super().__init__(master, fg_color=th.CARD, corner_radius=14, border_width=1,
                         border_color=th.BORDER)
        self.grid_columnconfigure(0, weight=1)
        self.value = ctk.CTkLabel(self, text="0", font=th.font(30, "bold"), text_color=color)
        self.value.grid(row=0, column=0, padx=16, pady=(12, 0), sticky="w")
        self.title = ctk.CTkLabel(self, text=title, font=th.font(12), text_color=th.MUTED)
        self.title.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="w")

    def set(self, n: int) -> None:
        text = str(n)
        if self.value.cget("text") != text:
            self.value.configure(text=text)


class JobRow(ctk.CTkFrame):
    """One file in the queue: name, details, status, progress and action buttons."""

    def __init__(self, master, job: Job, on_select, on_action):
        super().__init__(master, fg_color=th.CARD, corner_radius=12, border_width=1,
                         border_color=th.BORDER)
        self.job = job
        self._on_select = on_select
        self._on_action = on_action
        self._indeterminate = False
        self.grid_columnconfigure(0, weight=1)

        self.name_lbl = ctk.CTkLabel(self, text=job.name, anchor="w", font=th.font(14, "bold"),
                                     text_color=th.TEXT)
        self.name_lbl.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=(10, 0))
        self.meta_lbl = ctk.CTkLabel(self, text="", anchor="w", font=th.font(11), text_color=th.MUTED)
        self.meta_lbl.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 10))

        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.grid(row=0, column=1, rowspan=2, padx=8, pady=6)
        self.status_lbl = ctk.CTkLabel(mid, text="", font=th.font(12, "bold"), width=190, anchor="w",
                                       wraplength=190, justify="left")
        self.status_lbl.pack(anchor="w")
        self.bar = ctk.CTkProgressBar(mid, width=190, height=8, progress_color=th.ACCENT,
                                      fg_color=th.NAVY)
        self.bar.set(0)
        self.bar.pack(anchor="w", pady=(4, 2))
        self.time_lbl = ctk.CTkLabel(mid, text="", font=th.font(10), text_color=th.MUTED, anchor="w")
        self.time_lbl.pack(anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=2, padx=(4, 10))
        self.b_preview = self._btn(btns, "◉", "preview")
        self.b_compare = self._btn(btns, "⇄", "compare")
        self.b_retry = self._btn(btns, "↻", "retry")
        self.b_stop = self._btn(btns, "■", "cancel")
        self.b_remove = self._btn(btns, "✕", "remove")
        for i, b in enumerate((self.b_preview, self.b_compare, self.b_retry, self.b_stop, self.b_remove)):
            b.grid(row=0, column=i, padx=2)

        for w in (self, self.name_lbl, self.meta_lbl, self.status_lbl, self.time_lbl):
            w.bind("<Button-1>", lambda _e: self._on_select(self.job.id))
        self.update_view()

    def _btn(self, parent, glyph: str, action: str) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=glyph, width=34, height=30, corner_radius=8,
                             fg_color=th.CARD2, hover_color=th.ACCENT_DARK, text_color=th.TEXT,
                             font=th.font(14),
                             command=lambda: self._on_action(action, self.job.id))

    def select(self, on: bool) -> None:
        self.configure(border_color=th.ACCENT if on else th.BORDER)

    def update_view(self) -> None:
        j = self.job
        kind = t("kind_image") if j.kind == "image" else t("kind_video")
        parts = [f"{kind} {j.path.suffix.upper().lstrip('.')}", human_size(j.size)]
        if j.width:
            res = f"{j.width}×{j.height}"
            if j.out_w:
                res += f"  →  {j.out_w}×{j.out_h}"
            parts.append(res)
        if j.kind == "video" and j.duration:
            parts.append(human_time(j.duration))
        if j.probe_error:
            parts.append(t("probe_failed"))
        self.meta_lbl.configure(text="  •  ".join(parts))

        color = th.STATUS_COLORS.get(j.status, th.MUTED)
        text = t("st_" + j.status)
        if j.status == Status.RUNNING:
            text += f"  {int(j.progress * 100)}%"
        elif j.status in (Status.FAILED, Status.CANCELLED) and j.message:
            text += f": {j.message}"
        self.status_lbl.configure(text=text[:160], text_color=color)

        if j.status == Status.RUNNING and j.progress <= 0.001:
            if not self._indeterminate:
                self.bar.configure(mode="indeterminate")
                self.bar.start()
                self._indeterminate = True
        else:
            if self._indeterminate:
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self._indeterminate = False
            self.bar.set(1.0 if j.status == Status.DONE else j.progress)
        self.bar.configure(progress_color=th.OK if j.status == Status.DONE
                           else th.ERR if j.status == Status.FAILED else th.ACCENT)

        if j.status == Status.RUNNING:
            self.time_lbl.configure(text=t("time_line", elapsed=human_time(j.elapsed),
                                           eta=human_time(j.eta)))
        elif j.status == Status.DONE:
            self.time_lbl.configure(text=t("time_total", elapsed=human_time(j.elapsed)))
        else:
            self.time_lbl.configure(text="")

        self._show(self.b_compare, j.status == Status.DONE)
        self._show(self.b_retry, j.status in (Status.FAILED, Status.CANCELLED))
        self._show(self.b_stop, j.status in (Status.RUNNING, Status.PENDING))
        self._show(self.b_remove, j.status != Status.RUNNING)

    @staticmethod
    def _show(btn: ctk.CTkButton, visible: bool) -> None:
        if visible:
            btn.grid()
        else:
            btn.grid_remove()
