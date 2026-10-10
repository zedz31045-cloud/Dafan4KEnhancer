"""Settings-type pages (image, video, history, settings, about) as a mixin for the App."""
from __future__ import annotations

import customtkinter as ctk

from .. import APP_NAME, COPYRIGHT, __version__
from ..config import save_settings
from ..i18n import t
from ..jobs import resolve_output_dir  # noqa: F401  (re-exported for convenience)
from . import theme as th


class PagesMixin:
    # ------------------------------------------------------------------ building blocks
    def _new_page(self, name: str, title_key: str, sub_key: str, scroll: bool = True):
        page = ctk.CTkFrame(self.content, fg_color=th.BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 8))
        ctk.CTkLabel(head, text=t(title_key), font=th.font(26, "bold"), text_color=th.TEXT,
                     anchor=self.anchor, justify=self.justify).pack(fill="x")
        ctk.CTkLabel(head, text=t(sub_key), font=th.font(13), text_color=th.MUTED, anchor=self.anchor,
                     justify=self.justify, wraplength=900).pack(fill="x")
        if scroll:
            body = ctk.CTkScrollableFrame(page, fg_color="transparent", corner_radius=0)
        else:
            body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=1)
        self.pages[name] = page
        return body

    def _card(self, parent, title_key: str | None = None):
        card = ctk.CTkFrame(parent, fg_color=th.CARD, corner_radius=14, border_width=1,
                            border_color=th.BORDER)
        card.pack(fill="x", padx=10, pady=8)
        card.grid_columnconfigure(0, weight=1)
        r = 0
        if title_key:
            ctk.CTkLabel(card, text=t(title_key), font=th.font(15, "bold"), text_color=th.ACCENT,
                         anchor=self.anchor).grid(row=0, column=0, columnspan=2, sticky="ew",
                                                  padx=18, pady=(14, 4))
            r = 1
        card._next_row = r
        return card

    def _next(self, card) -> int:
        r = card._next_row
        card._next_row += 1
        return r

    def _note(self, parent, text: str, color: str = th.MUTED, size: int = 12) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(parent, text=text, font=th.font(size), text_color=color, anchor=self.anchor,
                           justify=self.justify, wraplength=860)
        r = self._next(parent)
        lbl.grid(row=r, column=0, columnspan=2, sticky="ew", padx=18, pady=(2, 10))
        return lbl

    def _label(self, card, key: str, r: int) -> None:
        ctk.CTkLabel(card, text=t(key), font=th.font(13), text_color=th.TEXT, anchor=self.anchor,
                     justify=self.justify).grid(row=r, column=0, sticky="ew", padx=18, pady=8)

    def _row_option(self, card, label_key: str, key: str, choices: list[tuple]):
        """Drop-down bound to a setting. `choices` is [(stored_value, shown_label)]."""
        r = self._next(card)
        self._label(card, label_key, r)
        labels = [lab for _, lab in choices]
        by_label = {lab: val for val, lab in choices}
        current = getattr(self.settings, key)
        shown = next((lab for val, lab in choices if val == current), labels[0])
        menu = ctk.CTkOptionMenu(
            card, values=labels, width=260, fg_color=th.CARD2, button_color=th.ACCENT_DARK,
            button_hover_color=th.ACCENT, dropdown_fg_color=th.PANEL, dropdown_hover_color=th.CARD2,
            text_color=th.TEXT, dropdown_text_color=th.TEXT, font=th.font(13),
            command=lambda lab: self._set(key, by_label[lab]))
        menu.set(shown)
        menu.grid(row=r, column=1, padx=18, pady=8, sticky="e")
        return menu

    def _row_slider(self, card, label_key: str, key: str, lo: int, hi: int):
        r = self._next(card)
        self._label(card, label_key, r)
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=r, column=1, padx=18, pady=8, sticky="e")
        val = ctk.CTkLabel(box, text=str(getattr(self.settings, key)), width=40, font=th.font(13, "bold"),
                           text_color=th.ACCENT)
        slider = ctk.CTkSlider(box, from_=lo, to=hi, number_of_steps=hi - lo, width=200,
                               progress_color=th.ACCENT, button_color=th.ACCENT,
                               button_hover_color=th.ACCENT_HOVER, fg_color=th.NAVY,
                               command=lambda v: (val.configure(text=str(int(v))), self._set(key, int(v))))
        slider.set(getattr(self.settings, key))
        slider.pack(side="left")
        val.pack(side="left", padx=(10, 0))

    def _row_int_entry(self, card, label_key: str, key: str, lo: int, hi: int):
        r = self._next(card)
        self._label(card, label_key, r)
        entry = ctk.CTkEntry(card, width=120, fg_color=th.CARD2, border_color=th.BORDER, text_color=th.TEXT)
        entry.insert(0, str(getattr(self.settings, key)))
        entry.grid(row=r, column=1, padx=18, pady=8, sticky="e")

        def commit(_e=None):
            try:
                value = int(entry.get().strip())
                if not lo <= value <= hi:
                    raise ValueError
            except ValueError:
                self._toast(t("bad_number", lo=lo, hi=hi), "err")
                entry.delete(0, "end")
                entry.insert(0, str(getattr(self.settings, key)))
                return
            self._set(key, value)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)

    # ------------------------------------------------------------------ image page
    def _build_image_page(self) -> None:
        body = self._new_page("image", "nav_image", "image_sub")
        c = self._card(body, "grp_quality")
        self._row_option(c, "lbl_preset", "image_preset",
                         [(k, t("preset_" + k)) for k in ("general", "portrait", "landscape", "old", "anime", "fast")])
        self._row_option(c, "lbl_scale", "image_scale", [(2, "2×"), (4, "4×")])
        self._row_option(c, "lbl_denoise", "image_denoise",
                         [(k, t("dn_" + k)) for k in ("auto", "none", "light", "medium", "strong")])
        self._note(c, t("preset_help"))
        c2 = self._card(body, "grp_output")
        self._row_option(c2, "lbl_format", "image_format",
                         [("same", t("fmt_same")), ("png", "PNG"), ("jpg", "JPG"), ("webp", "WEBP"),
                          ("tiff", "TIFF")])
        self._row_slider(c2, "lbl_jpg_quality", "jpg_quality", 60, 100)
        self._note(c2, t("image_output_note"))
        c3 = self._card(body, "grp_honesty")
        self._note(c3, t("ai_honesty"), th.WARN, 13)

    # ------------------------------------------------------------------ video page
    def _build_video_page(self) -> None:
        body = self._new_page("video", "nav_video", "video_sub")
        c = self._card(body, "grp_mode")
        r = self._next(c)
        seg = ctk.CTkSegmentedButton(
            c, values=[t("mode_ai"), t("mode_classic")], selected_color=th.ACCENT_DARK,
            selected_hover_color=th.ACCENT, unselected_color=th.CARD2, unselected_hover_color=th.NAVY,
            fg_color=th.NAVY, text_color=th.TEXT, font=th.font(13, "bold"),
            command=lambda lab: self._set_video_mode("ai" if lab == t("mode_ai") else "classic"))
        seg.set(t("mode_ai") if self.settings.video_mode == "ai" else t("mode_classic"))
        seg.grid(row=r, column=0, columnspan=2, padx=18, pady=8, sticky="ew")
        self.mode_desc = self._note(c, "", th.WARN, 13)
        self._set_video_mode(self.settings.video_mode, save=False)
        self._row_option(c, "lbl_ai_model", "video_model",
                         [("realesr-animevideov3", t("vm_animevideo")),
                          ("realesrgan-x4plus", t("vm_x4plus")),
                          ("realesrgan-x4plus-anime", t("vm_x4anime"))])

        c2 = self._card(body, "grp_resolution")
        self._row_option(c2, "lbl_target", "video_target",
                         [("1080p", "1080p (Full HD)"), ("1440p", "1440p (2K)"), ("2160p", "2160p (4K)"),
                          ("custom", t("target_custom"))])
        self._row_int_entry(c2, "lbl_custom_w", "custom_w", 160, 7680)
        self._row_int_entry(c2, "lbl_custom_h", "custom_h", 120, 4320)
        self._note(c2, t("target_note"))

        c3 = self._card(body, "grp_encode")
        self._row_option(c3, "lbl_codec", "video_codec", [("h264", "H.264 (x264)"), ("h265", "H.265 (x265)")])
        self._row_slider(c3, "lbl_crf", "video_crf", 10, 35)
        self._row_int_entry(c3, "lbl_bitrate", "video_bitrate_kbps", 0, 200000)
        self._note(c3, t("bitrate_note"))
        self._row_option(c3, "lbl_fps", "video_fps",
                         [("original", t("fps_original")), ("24", "24"), ("25", "25"), ("30", "30"),
                          ("50", "50"), ("60", "60")])
        self._note(c3, t("fps_note"))
        self._row_option(c3, "lbl_audio", "audio_mode",
                         [("copy", t("au_copy")), ("aac", t("au_aac")), ("none", t("au_none"))])
        self._row_option(c3, "lbl_container", "container", [("mp4", "MP4"), ("mkv", "MKV")])

    def _set_video_mode(self, mode: str, save: bool = True) -> None:
        if save:
            self._set("video_mode", mode)
        self.mode_desc.configure(text=t("mode_ai_desc") if mode == "ai" else t("mode_classic_desc"))

    # ------------------------------------------------------------------ history page
    def _build_history_page(self) -> None:
        body = self._new_page("history", "nav_history", "history_sub", scroll=False)
        body.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._btn(bar, t("btn_refresh"), self._refresh_history).pack(side="left", padx=(10, 6))
        self._btn(bar, t("btn_clear_history"), self._clear_history, secondary=True).pack(side="left", padx=6)
        self.history_box = ctk.CTkTextbox(body, fg_color=th.CARD, text_color=th.TEXT, font=th.font(12),
                                          border_width=1, border_color=th.BORDER, corner_radius=12, wrap="word")
        self.history_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 6))
        self._refresh_history()

    def _refresh_history(self) -> None:
        rows = self.manager.history.load(300)
        self.history_box.configure(state="normal")
        self.history_box.delete("1.0", "end")
        if not rows:
            self.history_box.insert("end", t("history_empty"))
        for r in rows:
            mark = {"done": "✔", "failed": "✖", "cancelled": "■"}.get(r.get("status"), "•")
            name = str(r.get("file", "")).replace("\\", "/").rsplit("/", 1)[-1]
            line = f"[{r.get('time', '')}] {mark} {name}  ({r.get('seconds', 0)}s)"
            if r.get("status") == "done" and r.get("output"):
                line += f"\n      → {r['output']}"
            elif r.get("message"):
                line += f"\n      {r['message']}"
            self.history_box.insert("end", line + "\n\n")
        self.history_box.configure(state="disabled")

    def _clear_history(self) -> None:
        self.manager.history.clear()
        self._refresh_history()

    # ------------------------------------------------------------------ settings page
    def _build_settings_page(self) -> None:
        body = self._new_page("settings", "nav_settings", "settings_sub")
        c = self._card(body, "grp_general")
        self._row_option(c, "lbl_language", "language", [("ar", "العربية"), ("en", "English")])
        self._folder_row(c, "lbl_output_dir", "output_dir", "out_default")
        self._folder_row(c, "lbl_temp_dir", "temp_dir", "temp_default")

        c2 = self._card(body, "grp_resources")
        self._row_option(c2, "lbl_memory", "memory",
                         [("low", t("mem_low")), ("balanced", t("mem_balanced")), ("high", t("mem_high"))])
        self._row_option(c2, "lbl_concurrent", "max_concurrent", [(1, "1"), (2, "2"), (3, "3")])
        self._row_option(c2, "lbl_chunk", "chunk_frames", [(60, "60"), (120, "120"), (240, "240")])
        self.device_menu = self._row_option(c2, "lbl_device", "device", self._device_choices())
        self._note(c2, t("resources_note"))
        r = self._next(c2)
        self._btn(c2, t("btn_detect_gpu"), self._detect_gpu).grid(row=r, column=0, padx=18, pady=(0, 10), sticky="w")
        self.gpu_lbl = ctk.CTkLabel(c2, text="", font=th.font(12), text_color=th.MUTED, anchor="w", wraplength=600)
        self.gpu_lbl.grid(row=r, column=1, padx=18, pady=(0, 10), sticky="e")

        c3 = self._card(body, "grp_tools")
        self.tool_lbls = {}
        for key in ("esrgan", "ffmpeg", "models"):
            r = self._next(c3)
            ctk.CTkLabel(c3, text=t("tool_" + key), font=th.font(13), text_color=th.TEXT,
                         anchor=self.anchor).grid(row=r, column=0, sticky="ew", padx=18, pady=4)
            lbl = ctk.CTkLabel(c3, text="", font=th.font(12, "bold"), anchor="e")
            lbl.grid(row=r, column=1, sticky="e", padx=18, pady=4)
            self.tool_lbls[key] = lbl
        r = self._next(c3)
        box = ctk.CTkFrame(c3, fg_color="transparent")
        box.grid(row=r, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 4))
        self.dl_btn = self._btn(box, t("btn_download_tools"), self._download_tools)
        self.dl_btn.pack(side="left", padx=4)
        self.dl_cancel_btn = self._btn(box, t("btn_cancel_download"), lambda: self._dl_cancel.set(), secondary=True)
        self._btn(box, t("btn_open_tools"), lambda: self._open_path(self._tools_folder()), secondary=True).pack(side="left", padx=4)
        self.dl_bar = ctk.CTkProgressBar(c3, height=8, progress_color=th.ACCENT, fg_color=th.NAVY)
        self.dl_bar.set(0)
        self.dl_bar.grid(row=self._next(c3), column=0, columnspan=2, sticky="ew", padx=18, pady=4)
        self.dl_bar.grid_remove()
        self.dl_lbl = self._note(c3, "")
        self._refresh_tools()

    def _folder_row(self, card, label_key: str, key: str, default_key: str) -> None:
        r = self._next(card)
        self._label(card, label_key, r)
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=r, column=1, padx=18, pady=8, sticky="e")
        value = getattr(self.settings, key)
        lbl = ctk.CTkLabel(box, text=value or t(default_key), font=th.font(12), text_color=th.MUTED,
                           width=260, anchor="e", wraplength=260)

        def choose():
            folder = self._ask_folder()
            if folder:
                self._set(key, folder)
                lbl.configure(text=folder)

        def reset():
            self._set(key, "")
            lbl.configure(text=t(default_key))

        lbl.pack(side="left", padx=(0, 8))
        self._btn(box, t("btn_browse"), choose, small=True).pack(side="left", padx=2)
        self._btn(box, t("btn_reset"), reset, small=True, secondary=True).pack(side="left", padx=2)

    def _device_choices(self) -> list[tuple]:
        choices = [("auto", t("dev_auto")), ("cpu", t("dev_cpu"))]
        known = set()
        for entry in self.gpus:
            idx, _, name = entry.partition(": ")
            choices.append((idx, f"GPU {idx}: {name}"))
            known.add(idx)
        cur = self.settings.device
        if cur.isdigit() and cur not in known:
            choices.append((cur, f"GPU {cur}"))
        return choices

    # ------------------------------------------------------------------ about page
    def _build_about_page(self) -> None:
        body = self._new_page("about", "nav_about", "about_sub")
        c = self._card(body)
        ctk.CTkLabel(c, text="DAFAN", font=th.font(40, "bold"), text_color=th.ACCENT).grid(row=0, column=0, padx=24, pady=(20, 0), sticky="w")
        ctk.CTkLabel(c, text="4K ENHANCER", font=th.font(18, "bold"), text_color=th.TEXT).grid(row=1, column=0, padx=24, sticky="w")
        ctk.CTkLabel(c, text=f"{t('lbl_version')}: {__version__}", font=th.font(13), text_color=th.MUTED).grid(row=2, column=0, padx=24, pady=(8, 0), sticky="w")
        ctk.CTkLabel(c, text=f"{COPYRIGHT} — {t('rights')}", font=th.font(13), text_color=th.MUTED).grid(row=3, column=0, padx=24, pady=(0, 14), sticky="w")
        c._next_row = 4
        c2 = self._card(body, "grp_about_tech")
        self._note(c2, t("about_tech"), th.TEXT, 13)
        self._note(c2, t("ai_honesty"), th.WARN, 13)
        self._note(c2, t("about_license"), th.MUTED, 12)

    # ------------------------------------------------------------------ small helpers
    def _btn(self, parent, text: str, command, secondary: bool = False, small: bool = False) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, command=command, corner_radius=10, height=30 if small else 36,
            width=0 if small else 120, font=th.font(12 if small else 13, "bold"),
            fg_color=th.CARD2 if secondary else th.ACCENT_DARK,
            hover_color=th.NAVY if secondary else th.ACCENT,
            text_color=th.TEXT, border_width=1 if secondary else 0, border_color=th.BORDER)

    def _set(self, key: str, value) -> None:
        setattr(self.settings, key, value)
        try:
            self.settings.validate()
        except (TypeError, ValueError):
            pass
        self._schedule_save()
        if key == "language":
            self.after(50, self._switch_language)
        else:
            self._refresh_summary()

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        try:
            save_settings(self.settings)
        except OSError as exc:
            self._toast(t("err_save_settings", err=str(exc)), "err")
