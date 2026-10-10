"""Before / after comparison window with a draggable divider."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

from ..i18n import t
from . import theme as th


class CompareWindow(ctk.CTkToplevel):
    def __init__(self, master, before: Image.Image, after: Image.Image, title: str, info: str):
        super().__init__(master)
        self.title(title)
        self.geometry("1100x760")
        self.minsize(640, 440)
        self.configure(fg_color=th.BG)
        self._before = before.convert("RGB")
        self._after = after.convert("RGB")
        self._split = 0.5
        self._cache: tuple | None = None
        self._photo = None

        head = ctk.CTkLabel(self, text=info, font=th.font(12), text_color=th.MUTED, anchor="w")
        head.pack(fill="x", padx=16, pady=(12, 4))
        self.canvas = tk.Canvas(self, bg=th.BG, highlightthickness=0, cursor="sb_h_double_arrow")
        self.canvas.pack(fill="both", expand=True, padx=12, pady=6)
        note = ctk.CTkLabel(self, text=t("compare_hint"), font=th.font(11), text_color=th.MUTED,
                            wraplength=1000, justify="left")
        note.pack(fill="x", padx=16, pady=(0, 12))
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<Button-1>", self._drag)
        self.after(50, self._redraw)

    # --- geometry -------------------------------------------------------------------
    def _fit(self, cw: int, ch: int) -> tuple[int, int]:
        w, h = self._after.size
        scale = min(cw / w, ch / h)
        return max(1, int(w * scale)), max(1, int(h * scale))

    def _scaled(self, size: tuple[int, int]):
        if self._cache and self._cache[0] == size:
            return self._cache[1], self._cache[2]
        b = self._before.resize(size, Image.BICUBIC)   # fair comparison: same pixel grid
        a = self._after.resize(size, Image.LANCZOS)
        self._cache = (size, b, a)
        return b, a

    def _drag(self, event) -> None:
        cw = max(1, self.canvas.winfo_width())
        iw, _ = self._fit(cw, max(1, self.canvas.winfo_height()))
        x0 = (cw - iw) // 2
        self._split = min(1.0, max(0.0, (event.x - x0) / max(1, iw)))
        self._redraw()

    def _redraw(self) -> None:
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 20 or ch < 20:
            return
        size = self._fit(cw, ch)
        before, after = self._scaled(size)
        w, h = size
        x = int(w * self._split)
        comp = Image.new("RGB", size)
        if x > 0:
            comp.paste(before.crop((0, 0, x, h)), (0, 0))
        if x < w:
            comp.paste(after.crop((x, 0, w, h)), (x, 0))
        self._photo = ImageTk.PhotoImage(comp)
        x0, y0 = (cw - w) // 2, (ch - h) // 2
        c = self.canvas
        c.delete("all")
        c.create_image(x0, y0, anchor="nw", image=self._photo)
        c.create_line(x0 + x, y0, x0 + x, y0 + h, fill=th.ACCENT, width=2)
        c.create_oval(x0 + x - 14, y0 + h // 2 - 14, x0 + x + 14, y0 + h // 2 + 14,
                      fill=th.ACCENT, outline=th.BG, width=2)
        c.create_text(x0 + x, y0 + h // 2, text="↔", fill=th.BG, font=("Segoe UI", 12, "bold"))
        c.create_text(x0 + 10, y0 + 10, anchor="nw", text=t("cmp_before"), fill=th.TEXT,
                      font=("Segoe UI", 11, "bold"))
        c.create_text(x0 + w - 10, y0 + 10, anchor="ne", text=t("cmp_after"), fill=th.ACCENT,
                      font=("Segoe UI", 11, "bold"))
