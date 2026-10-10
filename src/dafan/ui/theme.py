"""Colours and fonts: black / navy / sky-blue dark theme."""
import customtkinter as ctk

BG = "#070B14"
SIDEBAR = "#0A1020"
PANEL = "#0D1426"
CARD = "#111B33"
CARD2 = "#17274A"
BORDER = "#1E2E52"
NAVY = "#0B1F4B"
ACCENT = "#22D3EE"
ACCENT_HOVER = "#67E8F9"
ACCENT_DARK = "#0E7490"
TEXT = "#E6F1FF"
MUTED = "#8CA0C0"
OK = "#34D399"
ERR = "#F87171"
WARN = "#FBBF24"

FONT_FAMILY = "Segoe UI"

STATUS_COLORS = {
    "pending": MUTED,
    "running": ACCENT,
    "done": OK,
    "failed": ERR,
    "cancelled": WARN,
}


def font(size: int = 13, weight: str = "normal") -> "ctk.CTkFont":
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)
