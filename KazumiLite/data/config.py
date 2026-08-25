"""Shared paths, version information, and presentation constants."""

import os


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(SCRIPT_DIR, "font.ttf")
STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")
MPV_LOG_PATH = os.path.join(SCRIPT_DIR, "mpv.log")
DIAG_PATH = os.path.join(SCRIPT_DIR, "diagnostics.txt")
PINYIN_PATH = os.path.join(SCRIPT_DIR, "pinyin_words.tsv")
APP_VERSION = "0.2.3-r3"
KEYBOARD = "1234567890qwertyuiopasdfghjkl-zxcvbnm._?"

XIFAN_API = "https://rzmsnqblptbceicadbyd.supabase.co"
XIFAN_KEY = "sb_publishable_aCb7uwyLN6H-sMjze4dRGA_2MDuROLF"
AGE_BASE = "https://www.agedm.io"


class Palette:
    BG = (13, 16, 22, 255)
    PANEL = (25, 30, 40, 255)
    PANEL_FOCUS = (42, 48, 61, 255)
    ACCENT = (255, 116, 139, 255)
    MINT = (112, 214, 194, 255)
    WHITE = (244, 246, 250, 255)
    MUTED = (157, 166, 184, 255)
    GOOD = (108, 217, 139, 255)
    WARN = (255, 203, 107, 255)
    BAD = (255, 108, 108, 255)
    OVERLAY = (7, 9, 13, 238)


def env_int(name, fallback):
    try:
        value = int(os.environ.get(name, fallback))
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def format_time(seconds):
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"
