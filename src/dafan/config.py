"""User settings, stored as JSON in the user's data folder."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import user_data_dir

MEMORY_TILES = {"low": 128, "balanced": 256, "high": 512}


@dataclass
class Settings:
    language: str = "ar"
    # folders ("" = default behaviour)
    output_dir: str = ""        # empty -> "Dafan_Output" next to each source file
    temp_dir: str = ""          # empty -> user data dir / work
    # processing resources
    memory: str = "balanced"    # low | balanced | high  (controls tile size)
    max_concurrent: int = 1     # 1..3
    device: str = "auto"        # auto | cpu | gpu index as string ("0", "1"...)
    # images
    image_preset: str = "general"   # general portrait landscape old anime fast
    image_scale: int = 2            # 2 | 4
    image_denoise: str = "auto"     # auto none light medium strong
    image_format: str = "same"      # same png jpg webp tiff
    jpg_quality: int = 95
    # video
    video_mode: str = "ai"          # ai | classic
    video_target: str = "2160p"     # 1080p 1440p 2160p custom
    custom_w: int = 3840
    custom_h: int = 2160
    video_codec: str = "h264"       # h264 | h265
    video_crf: int = 18
    video_bitrate_kbps: int = 0     # 0 -> use CRF
    video_fps: str = "original"     # original 24 25 30 50 60
    audio_mode: str = "copy"        # copy | aac | none
    container: str = "mp4"          # mp4 | mkv
    video_model: str = "realesr-animevideov3"
    chunk_frames: int = 120

    def tile_size(self) -> int:
        return MEMORY_TILES.get(self.memory, 256)

    def validate(self) -> "Settings":
        self.max_concurrent = min(3, max(1, int(self.max_concurrent)))
        self.image_scale = 4 if int(self.image_scale) == 4 else 2
        self.jpg_quality = min(100, max(60, int(self.jpg_quality)))
        self.video_crf = min(35, max(10, int(self.video_crf)))
        self.video_bitrate_kbps = max(0, int(self.video_bitrate_kbps))
        self.chunk_frames = min(600, max(24, int(self.chunk_frames)))
        self.custom_w = min(7680, max(160, int(self.custom_w)))
        self.custom_h = min(4320, max(120, int(self.custom_h)))
        if self.memory not in MEMORY_TILES:
            self.memory = "balanced"
        if self.language not in ("ar", "en"):
            self.language = "ar"
        return self

    def copy(self) -> "Settings":
        return Settings(**asdict(self))


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    s = Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name: f for f in fields(Settings)}
        for key, value in data.items():
            if key in known:
                kind = type(getattr(s, key))
                try:
                    setattr(s, key, kind(value))
                except (TypeError, ValueError):
                    pass
    except (OSError, ValueError):
        pass
    try:
        return s.validate()
    except (TypeError, ValueError):
        return Settings()


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s.validate()), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
