#!/usr/bin/env python3
"""Controller-first Kazumi client for muOS Jacaranda."""

import ctypes
import json
import os
import platform
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback

from backend import AgeSource, CatalogClient, StateStore, USER_AGENT, XifanSource

try:
    import sdl2
    import sdl2.sdlttf as ttf
    from sdl2 import *
except Exception as exc:
    print(f"PySDL2 import failed: {exc}", flush=True)
    raise


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(SCRIPT_DIR, "font.ttf")
STATE_PATH = os.path.join(SCRIPT_DIR, "state.json")
MPV_LOG_PATH = os.path.join(SCRIPT_DIR, "mpv.log")
DIAG_PATH = os.path.join(SCRIPT_DIR, "diagnostics.txt")
PINYIN_PATH = os.path.join(SCRIPT_DIR, "pinyin_words.tsv")
APP_VERSION = "0.2.3-r1"
KEYBOARD = "1234567890qwertyuiopasdfghjkl-zxcvbnm._?"


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


class KazumiLiteApp:
    def __init__(self):
        self.width = env_int("APP_SCREEN_WIDTH", 640)
        self.height = env_int("APP_SCREEN_HEIGHT", 480)
        self.scale = max(0.75, min(1.6, self.height / 480.0))
        self.running = True
        self.page = "root"
        self.stack = []
        self.tab = 0
        self.selected = 0
        self.scroll = 0
        self.status = "准备就绪"
        self.status_kind = "good"
        self.controller = None
        self.joystick = None
        self.action_times = {}
        self.input_blocked_until = 0.0
        self.axis_x = 0
        self.axis_y = 0

        self.store = StateStore(STATE_PATH)
        self.catalog_client = CatalogClient()
        self.source = XifanSource()
        self.age_source = AgeSource()
        self.detail_source = self.source
        self.catalog = list(self.store.data["catalog"])
        self.search_results = []
        self.search_query = (
            self.store.data["queries"][0] if self.store.data["queries"] else ""
        )
        self.keyboard_index = 10
        self.keyboard_mode = "pinyin"
        self.pinyin_buffer = ""
        self.pinyin_candidates = []
        self.pinyin_candidate_index = 0
        self.pinyin_words = self.load_pinyin_words()
        self.detail = None
        self.source_index = 0
        self.pending_episode_id = None
        self.diagnostic_lines = []

        self.job_queue = queue.Queue()
        self.job_token = 0
        self.busy = False
        self.busy_message = ""

        SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if SDL_Init(SDL_INIT_VIDEO | SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER) != 0:
            raise RuntimeError(self.sdl_error("SDL 初始化失败"))
        if ttf.TTF_Init() != 0:
            raise RuntimeError(self.sdl_error("SDL_ttf 初始化失败"))

        self.window = SDL_CreateWindow(
            "Kazumi Lite".encode("utf-8"),
            SDL_WINDOWPOS_CENTERED,
            SDL_WINDOWPOS_CENTERED,
            self.width,
            self.height,
            SDL_WINDOW_SHOWN,
        )
        if not self.window:
            raise RuntimeError(self.sdl_error("窗口创建失败"))
        flags = SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC
        self.renderer = SDL_CreateRenderer(self.window, -1, flags)
        if not self.renderer:
            self.renderer = SDL_CreateRenderer(self.window, -1, SDL_RENDERER_SOFTWARE)
        if not self.renderer:
            raise RuntimeError(self.sdl_error("渲染器创建失败"))
        SDL_SetRenderDrawBlendMode(self.renderer, SDL_BLENDMODE_BLEND)

        if not os.path.isfile(FONT_PATH):
            raise RuntimeError(f"中文字体缺失：{FONT_PATH}")
        self.font_title = ttf.TTF_OpenFont(FONT_PATH.encode(), self.px(25))
        self.font_body = ttf.TTF_OpenFont(FONT_PATH.encode(), self.px(18))
        self.font_small = ttf.TTF_OpenFont(FONT_PATH.encode(), self.px(14))
        self.font_tiny = ttf.TTF_OpenFont(FONT_PATH.encode(), self.px(12))
        if not all((self.font_title, self.font_body, self.font_small, self.font_tiny)):
            raise RuntimeError(self.sdl_error("字体加载失败"))

        if SDL_NumJoysticks() > 0:
            if SDL_IsGameController(0):
                self.controller = SDL_GameControllerOpen(0)
                print("Input: SDL game controller", flush=True)
            else:
                self.joystick = SDL_JoystickOpen(0)
                print("Input: raw joystick", flush=True)
        SDL_StartTextInput()

        self.start_job(
            "正在更新热门番剧……",
            self.catalog_client.popular,
            self.catalog_loaded,
        )

    def px(self, value):
        return max(1, int(round(value * self.scale)))

    @staticmethod
    def load_pinyin_words():
        words = {}
        try:
            with open(PINYIN_PATH, "r", encoding="utf-8") as handle:
                for line in handle:
                    pinyin, _, values = line.rstrip("\n").partition("\t")
                    if pinyin and values:
                        words[pinyin] = values.split("|")
        except OSError as exc:
            print(f"Pinyin dictionary unavailable: {exc}", flush=True)
        return words

    def refresh_pinyin_candidates(self):
        self.pinyin_candidates = self.pinyin_words.get(self.pinyin_buffer, [])
        self.pinyin_candidate_index = 0

    def move_pinyin_candidate(self, delta):
        if not self.pinyin_candidates:
            return
        self.pinyin_candidate_index = (
            self.pinyin_candidate_index + delta
        ) % len(self.pinyin_candidates)
        print(
            "[ime] candidate="
            f"{self.pinyin_candidate_index + 1}/{len(self.pinyin_candidates)} "
            f"value={self.pinyin_candidates[self.pinyin_candidate_index]}",
            flush=True,
        )

    def visible_pinyin_candidates(self, max_width):
        """Return a candidate window that always contains the selection."""
        if not self.pinyin_candidates:
            return ""

        selected = min(
            max(0, self.pinyin_candidate_index),
            len(self.pinyin_candidates) - 1,
        )
        tokens = [
            f"[{value}]" if index == selected else value
            for index, value in enumerate(self.pinyin_candidates)
        ]
        start = selected
        end = selected + 1

        def make_line(first, last):
            left_more = "‹ " if first > 0 else ""
            right_more = " ›" if last < len(tokens) else ""
            return left_more + "  ".join(tokens[first:last]) + right_more

        # Grow around the selected candidate while the complete line fits.
        # Alternating sides preserves context without ever pushing the cursor
        # outside the visible area.
        prefer_left = True
        while start > 0 or end < len(tokens):
            changed = False
            choices = ("left", "right") if prefer_left else ("right", "left")
            for side in choices:
                new_start = start - 1 if side == "left" and start > 0 else start
                new_end = end + 1 if side == "right" and end < len(tokens) else end
                if new_start == start and new_end == end:
                    continue
                if self.measure(make_line(new_start, new_end), self.font_tiny)[0] <= max_width:
                    start, end = new_start, new_end
                    changed = True
                    prefer_left = not prefer_left
                    break
            if not changed:
                break
        return make_line(start, end)

    def commit_pinyin(self, add_space=False):
        if self.pinyin_buffer:
            if self.pinyin_candidates:
                self.search_query += self.pinyin_candidates[self.pinyin_candidate_index]
            else:
                # Keep raw pinyin usable for sources such as the 稀饭 API.
                self.search_query += self.pinyin_buffer
        self.pinyin_buffer = ""
        self.pinyin_candidates = []
        self.pinyin_candidate_index = 0
        if add_space:
            self.search_query += " "

    @staticmethod
    def sdl_error(prefix):
        raw = SDL_GetError()
        detail = raw.decode("utf-8", "replace") if raw else "unknown"
        return f"{prefix}: {detail}"

    def set_color(self, color):
        SDL_SetRenderDrawColor(self.renderer, *color)

    def rect(self, x, y, w, h, color):
        self.set_color(color)
        SDL_RenderFillRect(self.renderer, SDL_Rect(int(x), int(y), int(w), int(h)))

    @staticmethod
    def measure(value, font):
        width = ctypes.c_int()
        height = ctypes.c_int()
        result = ttf.TTF_SizeUTF8(
            font,
            str(value).encode("utf-8"),
            ctypes.byref(width),
            ctypes.byref(height),
        )
        return (width.value, height.value) if result == 0 else (0, 0)

    def ellipsize(self, value, font, max_width):
        value = str(value)
        if self.measure(value, font)[0] <= max_width:
            return value
        suffix = "…"
        while value and self.measure(value + suffix, font)[0] > max_width:
            value = value[:-1]
        return value + suffix

    def text(self, value, x, y, font=None, color=None, max_width=0):
        if value is None or value == "":
            return 0, 0
        font = font or self.font_body
        color = color or Palette.WHITE
        shown = self.ellipsize(value, font, max_width) if max_width else str(value)
        surface = ttf.TTF_RenderUTF8_Blended(
            font, shown.encode("utf-8"), SDL_Color(*color)
        )
        if not surface:
            print(self.sdl_error(f"Text render failed for {shown!r}"), flush=True)
            return 0, 0
        texture = SDL_CreateTextureFromSurface(self.renderer, surface)
        width, height = surface.contents.w, surface.contents.h
        SDL_FreeSurface(surface)
        if texture:
            SDL_RenderCopy(
                self.renderer,
                texture,
                None,
                SDL_Rect(int(x), int(y), int(width), int(height)),
            )
            SDL_DestroyTexture(texture)
        return width, height

    def render(self):
        # A real clear is required after MPV releases the DRM framebuffer. A
        # blended full-screen rectangle can leave video data in stale buffers.
        SDL_SetRenderDrawBlendMode(self.renderer, SDL_BLENDMODE_NONE)
        self.set_color(Palette.BG)
        SDL_RenderClear(self.renderer)
        SDL_SetRenderDrawBlendMode(self.renderer, SDL_BLENDMODE_BLEND)
        if self.page == "root":
            self.render_root()
        elif self.page == "results":
            self.render_results()
        elif self.page == "detail":
            self.render_detail()
        elif self.page == "keyboard":
            self.render_keyboard()
        elif self.page == "diagnostics":
            self.render_diagnostics()
        elif self.page == "about":
            self.render_about()
        self.render_footer()
        if self.busy:
            self.render_busy()
        SDL_RenderPresent(self.renderer)

    def render_header(self, title, badge=""):
        margin = self.px(20)
        badge_w = 0
        if badge:
            badge_w = min(
                self.px(145), self.measure(badge, self.font_tiny)[0] + self.px(20)
            )
        title_width = self.width - margin * 2 - badge_w
        if badge_w:
            title_width -= self.px(12)
        self.text(
            title,
            margin,
            self.px(13),
            self.font_title,
            Palette.WHITE,
            title_width,
        )
        if badge:
            x = self.width - margin - badge_w
            self.rect(x, self.px(18), badge_w, self.px(24), Palette.PANEL_FOCUS)
            self.text(
                badge,
                x + self.px(10),
                self.px(23),
                self.font_tiny,
                Palette.MINT,
                badge_w - self.px(20),
            )
        self.rect(
            margin,
            self.px(50),
            self.width - margin * 2,
            self.px(2),
            Palette.ACCENT,
        )

    def render_root(self):
        self.render_header("Kazumi Lite", f"v{APP_VERSION}")
        labels = ("热门", "收藏", "历史", "设置")
        x = self.px(20)
        y = self.px(61)
        tab_w = (self.width - self.px(40)) // len(labels)
        for index, label in enumerate(labels):
            focused = index == self.tab
            self.rect(
                x + index * tab_w,
                y,
                tab_w - self.px(3),
                self.px(34),
                Palette.PANEL_FOCUS if focused else Palette.PANEL,
            )
            self.text(
                label,
                x + index * tab_w + self.px(12),
                y + self.px(8),
                self.font_small,
                Palette.MINT if focused else Palette.MUTED,
            )
        self.render_list(self.root_items(), self.px(104), self.px(64), 5)

    def render_results(self):
        self.render_header(f"搜索：{self.search_query}", f"{len(self.search_results)} 项")
        self.render_list(self.search_results, self.px(61), self.px(61), 6)

    def render_detail(self):
        if not self.detail:
            return
        anime = self.detail["anime"]
        favorite = self.store.is_favorite(anime["id"])
        self.render_header(anime.get("title") or "番剧详情", "已收藏" if favorite else "X 收藏")
        sources = self.detail["sources"]
        source = sources[self.source_index]
        score = anime.get("bangumi_score") or 0
        year = anime.get("release_year") or ""
        info = f"{year}  评分 {score:g}  线路 {self.source_index + 1}/{len(sources)}：{source['name']}"
        self.text(
            info,
            self.px(20),
            self.px(64),
            self.font_tiny,
            Palette.MINT,
            self.width - self.px(40),
        )
        self.render_list(
            source["episodes"],
            self.px(88),
            self.px(48),
            7,
            title_key="label",
        )

    def render_keyboard(self):
        mode = "拼音输入" if self.keyboard_mode == "pinyin" else "直接输入"
        self.render_header("搜索番剧", f"{mode}  SELECT 切换")
        self.rect(
            self.px(20),
            self.px(64),
            self.width - self.px(40),
            self.px(48),
            Palette.PANEL,
        )
        placeholder = "输入英文名，例如 frieren"
        composing = self.search_query
        if self.keyboard_mode == "pinyin" and self.pinyin_buffer:
            composing += f" [{self.pinyin_buffer}]"
        self.text(
            composing or placeholder,
            self.px(34),
            self.px(78),
            self.font_body,
            Palette.WHITE if composing else Palette.MUTED,
            self.width - self.px(68),
        )
        cell_w = (self.width - self.px(40)) // 10
        cell_h = self.px(55)
        top = self.px(125)
        for index, char in enumerate(KEYBOARD):
            row, column = divmod(index, 10)
            x = self.px(20) + column * cell_w
            y = top + row * cell_h
            focused = index == self.keyboard_index
            self.rect(
                x,
                y,
                cell_w - self.px(4),
                cell_h - self.px(4),
                Palette.PANEL_FOCUS if focused else Palette.PANEL,
            )
            shown = char.upper()
            text_w, _ = self.measure(shown, self.font_body)
            self.text(
                shown,
                x + (cell_w - self.px(4) - text_w) // 2,
                y + self.px(14),
                self.font_body,
                Palette.MINT if focused else Palette.WHITE,
            )
        if self.keyboard_mode == "pinyin" and self.pinyin_candidates:
            label = (
                f"候选 {self.pinyin_candidate_index + 1}/"
                f"{len(self.pinyin_candidates)}："
            )
            label_width, _ = self.measure(label, self.font_tiny)
            available_width = self.width - self.px(48) - label_width
            candidate_text = label + self.visible_pinyin_candidates(available_width)
            self.text(
                candidate_text,
                self.px(20),
                self.px(315),
                self.font_tiny,
                Palette.MINT,
                self.width - self.px(40),
            )
        recent = "最近：" + " / ".join(self.store.data["queries"][:3])
        self.text(
            recent,
            self.px(20),
            self.px(365),
            self.font_tiny,
            Palette.MUTED,
            self.width - self.px(40),
        )

    def render_diagnostics(self):
        self.render_header("运行环境检查", "B 返回")
        y = self.px(71)
        for line in self.diagnostic_lines:
            color = Palette.GOOD if line.startswith("✓") else Palette.BAD
            self.text(
                line,
                self.px(22),
                y,
                self.font_small,
                color,
                self.width - self.px(44),
            )
            y += self.px(39)

    def render_about(self):
        self.render_header("关于 Kazumi Lite", f"v{APP_VERSION}")
        lines = [
            "为 muOS Jacaranda 编写的非官方轻量客户端",
            "番剧元数据：Bangumi / Kazumi 镜像",
            "播放规则：KazumiRules（MIT）",
            "播放器：muOS / PortMaster MPV",
            "本程序不提供、上传或存储视频内容",
            "收藏与历史只保存在 data/state.json",
        ]
        y = self.px(76)
        for line in lines:
            self.text(
                line,
                self.px(24),
                y,
                self.font_small,
                Palette.WHITE,
                self.width - self.px(48),
            )
            y += self.px(45)

    def render_list(self, items, top, row_h, visible, title_key="title"):
        if not items:
            self.text(
                "这里还没有内容",
                self.px(24),
                top + self.px(24),
                self.font_body,
                Palette.MUTED,
            )
            return
        for slot in range(visible):
            index = self.scroll + slot
            if index >= len(items):
                break
            item = items[index]
            y = top + slot * row_h
            focused = index == self.selected
            self.rect(
                self.px(20),
                y,
                self.width - self.px(40),
                row_h - self.px(5),
                Palette.PANEL_FOCUS if focused else Palette.PANEL,
            )
            if focused:
                self.rect(
                    self.px(20),
                    y,
                    self.px(5),
                    row_h - self.px(5),
                    Palette.ACCENT,
                )
            title = item.get(title_key) or item.get("title") or "未命名"
            subtitle = self.item_subtitle(item)
            self.text(
                title,
                self.px(36),
                y + self.px(7),
                self.font_small,
                Palette.WHITE if focused else Palette.MUTED,
                self.width - self.px(72),
            )
            if subtitle:
                self.text(
                    subtitle,
                    self.px(36),
                    y + self.px(31),
                    self.font_tiny,
                    Palette.MINT if focused else Palette.MUTED,
                    self.width - self.px(72),
                )

    @staticmethod
    def item_subtitle(item):
        if "episode" in item:
            progress = format_time(item.get("position"))
            duration = format_time(item.get("duration"))
            return f"{item['episode']}  {progress} / {duration}"
        return item.get("subtitle") or ""

    def render_footer(self):
        y = self.height - self.px(43)
        self.rect(0, y, self.width, self.px(43), Palette.PANEL)
        if self.page == "keyboard":
            hint = "方向键选字母  L1/R1 选候选  A 输入  Y 确认/空格  X 删除  SELECT 切换"
        elif self.page == "detail":
            hint = "A 播放  X 收藏  L/R 换线路  Y 搜索  B 返回"
        elif self.page == "root":
            hint = "方向键选择  A 确认  Y 搜索  START 刷新  B 退出"
        else:
            hint = "方向键选择  A 确认  Y 搜索  B 返回"
        if self.status_kind == "bad":
            hint = self.status
        color = Palette.BAD if self.status_kind == "bad" else Palette.MUTED
        self.text(
            hint,
            self.px(20),
            y + self.px(13),
            self.font_tiny,
            color,
            self.width - self.px(40),
        )

    def render_busy(self):
        band_y = self.px(193)
        self.rect(0, band_y, self.width, self.px(82), Palette.OVERLAY)
        self.rect(
            self.px(20), band_y, self.px(5), self.px(82), Palette.MINT
        )
        self.text(
            self.busy_message,
            self.px(42),
            band_y + self.px(21),
            self.font_body,
            Palette.WHITE,
            self.width - self.px(80),
        )
        self.text(
            "B 可取消等待",
            self.px(42),
            band_y + self.px(51),
            self.font_tiny,
            Palette.MUTED,
        )

    def root_items(self):
        if self.tab == 0:
            return self.catalog
        if self.tab == 1:
            return self.store.data["favorites"]
        if self.tab == 2:
            return self.store.data["history"]
        return [
            {"title": "当前视频源", "subtitle": self.source.name},
            {"title": "刷新热门目录", "subtitle": "重新获取最新番剧"},
            {"title": "运行环境检查", "subtitle": "MPV、按键与网络"},
            {"title": "关于与许可", "subtitle": f"Kazumi Lite {APP_VERSION}"},
            {"title": "退出应用", "subtitle": "返回 muOS"},
        ]

    def current_items(self):
        if self.page == "root":
            return self.root_items()
        if self.page == "results":
            return self.search_results
        if self.page == "detail" and self.detail:
            return self.detail["sources"][self.source_index]["episodes"]
        return []

    def visible_count(self):
        return {"root": 5, "results": 6, "detail": 7}.get(self.page, 1)

    def move(self, delta):
        items = self.current_items()
        if not items:
            return
        self.selected = (self.selected + delta) % len(items)
        visible = self.visible_count()
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + visible:
            self.scroll = self.selected - visible + 1

    def switch_horizontal(self, delta):
        if self.page == "root":
            self.tab = (self.tab + delta) % 4
            self.selected = 0
            self.scroll = 0
        elif self.page == "detail" and self.detail:
            sources = self.detail["sources"]
            self.source_index = (self.source_index + delta) % len(sources)
            self.selected = 0
            self.scroll = 0

    def push_page(self, page):
        self.stack.append((self.page, self.selected, self.scroll, self.tab))
        self.page = page
        self.selected = 0
        self.scroll = 0

    def restore_page(self):
        if not self.stack:
            return False
        self.page, self.selected, self.scroll, self.tab = self.stack.pop()
        return True

    def start_job(self, message, function, callback):
        if self.busy:
            return
        self.job_token += 1
        token = self.job_token
        self.busy = True
        self.busy_message = message
        self.status = message
        self.status_kind = "warn"

        def worker():
            try:
                result = function()
                self.job_queue.put((token, callback, result, None))
            except Exception as exc:
                traceback.print_exc()
                self.job_queue.put((token, callback, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_job(self):
        if not self.busy:
            return
        self.job_token += 1
        self.busy = False
        self.busy_message = ""
        self.status = "已取消等待；后台请求会自行结束。"
        self.status_kind = "warn"

    def poll_jobs(self):
        while True:
            try:
                token, callback, result, error = self.job_queue.get_nowait()
            except queue.Empty:
                return
            if token != self.job_token:
                continue
            self.busy = False
            self.busy_message = ""
            if error:
                self.status = str(error)
                self.status_kind = "bad"
                print(f"[job] {error}", flush=True)
            else:
                try:
                    callback(result)
                except Exception as exc:
                    traceback.print_exc()
                    self.status = f"处理结果失败：{exc}"
                    self.status_kind = "bad"

    def catalog_loaded(self, items):
        self.catalog = items
        try:
            self.store.set_catalog(items)
        except OSError as exc:
            print(f"State save failed: {exc}", flush=True)
        self.status = f"热门目录已更新，共 {len(items)} 项。"
        self.status_kind = "good"

    def open_keyboard(self):
        if self.page == "keyboard":
            return
        self.push_page("keyboard")
        self.keyboard_index = 10
        self.pinyin_buffer = ""
        self.pinyin_candidates = []
        self.pinyin_candidate_index = 0

    def submit_search(self, query=None, from_keyboard=False):
        if query is None:
            query = self.search_query
            if self.page == "keyboard" and self.keyboard_mode == "pinyin":
                query += self.pinyin_buffer
        query = query.strip()
        if not query:
            self.status = "请先输入搜索词。"
            self.status_kind = "bad"
            return
        self.search_query = query
        self.start_job(
            f"正在搜索 {query}……",
            lambda: self.search_all_sources(query),
            lambda items: self.search_loaded(items, from_keyboard),
        )

    def search_all_sources(self, query):
        results = []
        try:
            results.extend(self.source.search(query))
        except Exception as exc:
            print(f"[search] 稀饭源失败：{exc}", flush=True)
        try:
            age_results = self.age_source.search(query)
            for item in age_results:
                item["provider"] = "age"
            results.extend(age_results)
        except Exception as exc:
            print(f"[search] AGE 源失败：{exc}", flush=True)
        return results

    def search_loaded(self, items, from_keyboard):
        self.search_results = items
        try:
            self.store.add_query(self.search_query)
        except OSError as exc:
            print(f"Search history save failed: {exc}", flush=True)
        if from_keyboard:
            self.page = "results"
            self.selected = 0
            self.scroll = 0
        else:
            self.push_page("results")
        self.status = f"找到 {len(items)} 个结果。" if items else "没有找到匹配条目。"
        self.status_kind = "good" if items else "bad"

    def open_detail(self, anime_id, preferred_episode=None):
        self.pending_episode_id = preferred_episode
        self.detail_source = (
            self.age_source
            if isinstance(anime_id, str) and anime_id.startswith("http")
            else self.source
        )
        self.start_job(
            "正在加载剧集与线路……",
            lambda: self.detail_source.detail(anime_id),
            self.detail_loaded,
        )

    def detail_loaded(self, detail):
        self.detail = detail
        self.source_index = 0
        anime_id = int(detail["anime"]["id"])
        history = next(
            (
                item
                for item in self.store.data["history"]
                if str(item.get("anime_id", "")) == str(anime_id)
            ),
            None,
        )
        if history:
            progress = float(history.get("position") or 0)
            duration = float(history.get("duration") or 0)
            progress_text = (
                "已看完"
                if duration > 0 and progress / duration >= 0.92
                else f"上次看到 {format_time(progress)}"
            )
            for source in detail["sources"]:
                for episode in source["episodes"]:
                    if str(episode["id"]) == str(history.get("episode_id", "")):
                        episode["subtitle"] = progress_text
        self.push_page("detail")
        if self.pending_episode_id is not None:
            target = str(self.pending_episode_id)
            for source_index, source in enumerate(detail["sources"]):
                for episode_index, episode in enumerate(source["episodes"]):
                    if str(episode["id"]) == target:
                        self.source_index = source_index
                        self.selected = episode_index
                        self.scroll = max(0, episode_index - 3)
                        break
                else:
                    continue
                break
        self.pending_episode_id = None
        count = len(detail["sources"][self.source_index]["episodes"])
        self.status = f"已加载 {len(detail['sources'])} 条线路、{count} 个剧集。"
        self.status_kind = "good"

    def toggle_favorite(self):
        if not self.detail:
            return
        anime = self.detail["anime"]
        try:
            active = self.store.toggle_favorite(
                anime["id"],
                anime.get("title") or "未命名",
                self.source.anime_subtitle(anime),
            )
        except OSError as exc:
            self.status = f"收藏保存失败：{exc}"
            self.status_kind = "bad"
            return
        self.status = "已加入收藏。" if active else "已取消收藏。"
        self.status_kind = "good"

    def play_episode(self):
        if not self.detail:
            return
        episode = self.current_items()[self.selected]
        self.start_job(
            "正在获取 480p 播放地址……",
            lambda: self.detail_source.playback(episode["id"], 480),
            lambda playback: self.launch_mpv(playback, episode),
        )

    def find_mpv(self):
        candidates = [
            shutil.which("mpv"),
            "/usr/bin/mpv",
            "/opt/system/Tools/PortMaster/libs/mpv",
            "/opt/tools/PortMaster/libs/mpv",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    @staticmethod
    def mpv_request(path, command, expect_reply=False):
        if not path or not os.path.exists(path):
            return None
        client = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(0.35)
            client.connect(path)
            client.sendall((json.dumps({"command": command}) + "\n").encode("utf-8"))
            if not expect_reply:
                return True
            chunks = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            response = json.loads(
                b"".join(chunks).split(b"\n", 1)[0].decode("utf-8")
            )
            return response.get("data") if response.get("error") == "success" else None
        except Exception:
            return None
        finally:
            if client:
                client.close()

    def player_action_allowed(self, actions, name, window=0.12):
        now = time.monotonic()
        if now - actions.get(name, 0.0) < window:
            return False
        actions[name] = now
        return True

    def run_mpv(self, mpv, url, episode, resume, quality):
        ipc_path = f"/tmp/kazumilite-mpv-{os.getpid()}.sock"
        try:
            if os.path.exists(ipc_path):
                os.remove(ipc_path)
        except OSError:
            pass
        command = [
            mpv,
            "--fs",
            "--no-terminal",
            "--keep-open=no",
            "--cache=yes",
            "--demuxer-max-bytes=32M",
            "--vd-lavc-threads=4",
            "--tls-verify=no",
            "--referrer=https://next.xifanacg.com/",
            f"--user-agent={USER_AGENT}",
            f"--input-ipc-server={ipc_path}",
            f"--log-file={MPV_LOG_PATH}",
            "--osd-level=1",
            "--osd-on-seek=bar",
        ]
        if resume >= 15:
            command.append(f"--start={resume:.1f}")
        command.append(url)
        print(
            f"[playback] episode={episode['id']} quality={quality} resume={resume:.1f}",
            flush=True,
        )
        process = None
        position = resume
        duration = 0.0
        user_quit = False
        started = time.monotonic()
        last_query = 0.0
        actions = {}
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
            event = SDL_Event()
            while process.poll() is None:
                while SDL_PollEvent(event):
                    if event.type == SDL_QUIT:
                        user_quit = True
                        process.terminate()
                    elif event.type == SDL_CONTROLLERBUTTONDOWN:
                        button = event.cbutton.button
                        if button == SDL_CONTROLLER_BUTTON_B:
                            user_quit = True
                            if not self.mpv_request(ipc_path, ["quit"]):
                                process.terminate()
                        elif button in (
                            SDL_CONTROLLER_BUTTON_A,
                            SDL_CONTROLLER_BUTTON_Y,
                        ):
                            if self.player_action_allowed(actions, "pause", 0.2):
                                self.mpv_request(ipc_path, ["cycle", "pause"])
                        elif button in (
                            SDL_CONTROLLER_BUTTON_DPAD_LEFT,
                            SDL_CONTROLLER_BUTTON_LEFTSHOULDER,
                        ):
                            if self.player_action_allowed(actions, "left"):
                                self.mpv_request(ipc_path, ["seek", -10, "relative"])
                        elif button in (
                            SDL_CONTROLLER_BUTTON_DPAD_RIGHT,
                            SDL_CONTROLLER_BUTTON_RIGHTSHOULDER,
                        ):
                            if self.player_action_allowed(actions, "right"):
                                self.mpv_request(ipc_path, ["seek", 10, "relative"])
                    elif event.type == SDL_JOYHATMOTION:
                        if event.jhat.value & SDL_HAT_LEFT:
                            if self.player_action_allowed(actions, "left"):
                                self.mpv_request(ipc_path, ["seek", -10, "relative"])
                        elif event.jhat.value & SDL_HAT_RIGHT:
                            if self.player_action_allowed(actions, "right"):
                                self.mpv_request(ipc_path, ["seek", 10, "relative"])
                    elif event.type == SDL_KEYDOWN:
                        key = event.key.keysym.sym
                        if key in (SDLK_ESCAPE, SDLK_x):
                            user_quit = True
                            if not self.mpv_request(ipc_path, ["quit"]):
                                process.terminate()
                        elif key in (SDLK_SPACE, SDLK_z):
                            self.mpv_request(ipc_path, ["cycle", "pause"])
                        elif key == SDLK_LEFT:
                            self.mpv_request(ipc_path, ["seek", -10, "relative"])
                        elif key == SDLK_RIGHT:
                            self.mpv_request(ipc_path, ["seek", 10, "relative"])
                now = time.monotonic()
                if now - last_query >= 1.0:
                    current = self.mpv_request(
                        ipc_path, ["get_property", "time-pos"], True
                    )
                    total = self.mpv_request(
                        ipc_path, ["get_property", "duration"], True
                    )
                    if isinstance(current, (int, float)):
                        position = float(current)
                    if isinstance(total, (int, float)):
                        duration = float(total)
                    last_query = now
                SDL_Delay(25)
            code = process.returncode
        except Exception:
            traceback.print_exc()
            code = -1
        finally:
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    process.kill()
            try:
                if os.path.exists(ipc_path):
                    os.remove(ipc_path)
            except OSError:
                pass
        return {
            "code": code,
            "position": position,
            "duration": duration,
            "elapsed": time.monotonic() - started,
            "user_quit": user_quit,
        }

    def launch_mpv(self, playback, episode):
        mpv = self.find_mpv()
        if not mpv:
            self.status = "没有找到 MPV；请打开设置里的环境检查。"
            self.status_kind = "bad"
            return
        anime = self.detail["anime"]
        resume = self.store.playback_position(anime["id"], episode["id"])
        try:
            if os.path.exists(MPV_LOG_PATH):
                os.remove(MPV_LOG_PATH)
        except OSError:
            pass
        self.status = f"正在播放 {episode['label']}（{playback['quality']}）"
        self.status_kind = "good"
        self.render()
        # Keep the SDL window alive under MPV. Hiding it lets muOS or stale
        # video buffers show through while MPV hands the display back.
        SDL_Delay(80)
        result = self.run_mpv(
            mpv, playback["url"], episode, resume, playback["quality"]
        )
        should_fallback = (
            playback.get("fallback_url")
            and not result["user_quit"]
            and (result["code"] != 0 or (result["position"] < 1 and result["elapsed"] < 10))
        )
        if should_fallback:
            print("[playback] HLS failed; trying MP4 fallback", flush=True)
            result = self.run_mpv(
                mpv,
                playback["fallback_url"],
                episode,
                resume,
                "MP4 备用",
            )
        if result["position"] > 0.5:
            try:
                self.store.record_playback(
                    anime["id"],
                    anime.get("title") or "未命名",
                    episode["id"],
                    episode["label"],
                    result["position"],
                    result["duration"],
                )
            except OSError as exc:
                print(f"History save failed: {exc}", flush=True)
        if result["user_quit"] or result["code"] == 0:
            self.status = f"已返回：{episode['label']}"
            self.status_kind = "good"
        else:
            self.status = f"播放失败，MPV 错误码 {result['code']}；请查看 mpv.log。"
            self.status_kind = "bad"
        SDL_ShowWindow(self.window)
        SDL_SetWindowSize(self.window, self.width, self.height)
        SDL_SetWindowPosition(
            self.window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED
        )
        SDL_RaiseWindow(self.window)
        SDL_FlushEvents(SDL_FIRSTEVENT, SDL_LASTEVENT)
        # KMS/DRM commonly rotates through more than one framebuffer. Repaint
        # each one before accepting input so the last video frame cannot flash.
        for _ in range(3):
            self.render()
            SDL_Delay(18)
        self.input_blocked_until = time.monotonic() + 0.3

    def collect_diagnostics(self):
        mpv = self.find_mpv()
        control_dir = os.environ.get("KAZUMI_LITE_CONTROL_DIR", "")
        network = []
        for host in ("api.kazumi.fyi", "rzmsnqblptbceicadbyd.supabase.co"):
            try:
                socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                network.append((host, True))
            except Exception:
                network.append((host, False))
        lines = [
            f"✓ 屏幕：{self.width} × {self.height}",
            f"✓ Python：{platform.python_version()} / {platform.machine()}",
            f"✓ SDL2：{SDL_MAJOR_VERSION}.{SDL_MINOR_VERSION}.{SDL_PATCHLEVEL}",
            ("✓ MPV 可用" if mpv else "✕ 未找到 MPV"),
            ("✓ PortMaster 运行时" if control_dir else "✕ 未找到 PortMaster"),
            (
                "✓ RG35XX Pro 按键已识别"
                if (self.controller or self.joystick)
                else "✕ 未识别掌机按键"
            ),
            (
                "✓ 番剧接口域名可解析"
                if all(ok for _, ok in network)
                else "✕ 番剧接口域名解析失败"
            ),
        ]
        payload = [
            "Kazumi Lite diagnostics",
            f"time={time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"platform={platform.platform()}",
            f"python={sys.version}",
            f"screen={self.width}x{self.height}",
            f"mpv={mpv or 'missing'}",
            f"portmaster={control_dir or 'missing'}",
            f"controller={'yes' if self.controller else 'no'}",
            f"joystick={'yes' if self.joystick else 'no'}",
        ]
        payload.extend(f"dns_{host}={'ok' if ok else 'failed'}" for host, ok in network)
        return lines, payload

    def diagnostics_loaded(self, result):
        self.diagnostic_lines, payload = result
        try:
            with open(DIAG_PATH, "w", encoding="utf-8") as handle:
                handle.write("\n".join(payload) + "\n")
        except OSError as exc:
            print(f"Diagnostics save failed: {exc}", flush=True)
        print("\n".join(payload), flush=True)
        self.push_page("diagnostics")
        self.status = "检查完成。"
        self.status_kind = (
            "good" if all(line.startswith("✓") for line in self.diagnostic_lines) else "bad"
        )

    def run_diagnostics(self):
        self.start_job(
            "正在检查运行环境……",
            self.collect_diagnostics,
            self.diagnostics_loaded,
        )

    def confirm(self):
        if self.page == "keyboard":
            self.search_query += KEYBOARD[self.keyboard_index]
            return
        if self.page in ("diagnostics", "about"):
            return
        items = self.current_items()
        if not items:
            return
        item = items[self.selected]
        if self.page == "detail":
            self.play_episode()
        elif self.page == "results":
            self.open_detail(item["id"])
        elif self.page == "root":
            if self.tab == 0:
                self.open_detail(item["id"])
            elif self.tab == 1:
                self.open_detail(item["id"])
            elif self.tab == 2:
                self.open_detail(item["anime_id"], item.get("episode_id"))
            else:
                self.confirm_setting(self.selected)

    def confirm_setting(self, index):
        if index == 0:
            self.status = f"当前使用 {self.source.name}，默认选择 480p。"
            self.status_kind = "good"
        elif index == 1:
            self.start_job(
                "正在刷新热门目录……",
                self.catalog_client.popular,
                self.catalog_loaded,
            )
        elif index == 2:
            self.run_diagnostics()
        elif index == 3:
            self.push_page("about")
        else:
            self.running = False

    def back(self):
        if self.busy:
            self.cancel_job()
        elif self.page == "root":
            self.running = False
        else:
            self.restore_page()

    def keyboard_move(self, dx, dy):
        row, column = divmod(self.keyboard_index, 10)
        row = (row + dy) % 4
        row_start = row * 10
        row_length = min(10, max(0, len(KEYBOARD) - row_start))
        if dx:
            column = (column + dx) % row_length
        else:
            column = min(column, row_length - 1)
        self.keyboard_index = row_start + column

    def action_allowed(self, action):
        now = time.monotonic()
        if now < self.input_blocked_until:
            return False
        window = 0.12 if action in ("up", "down", "left", "right") else 0.24
        if action == "back":
            window = 0.35
        if now - self.action_times.get(action, 0.0) < window:
            return False
        self.action_times[action] = now
        return True

    def handle_action(self, action):
        if not self.action_allowed(action):
            return
        print(f"[input] action={action} page={self.page}", flush=True)
        if self.busy:
            if action == "back":
                self.back()
            return
        if self.page == "keyboard":
            if action == "up":
                self.keyboard_move(0, -1)
            elif action == "down":
                self.keyboard_move(0, 1)
            elif action == "left":
                self.keyboard_move(-1, 0)
            elif action == "right":
                self.keyboard_move(1, 0)
            elif action == "candidate_left":
                if self.keyboard_mode == "pinyin":
                    self.move_pinyin_candidate(-1)
            elif action == "candidate_right":
                if self.keyboard_mode == "pinyin":
                    self.move_pinyin_candidate(1)
            elif action == "confirm":
                char = KEYBOARD[self.keyboard_index]
                if self.keyboard_mode == "pinyin":
                    self.pinyin_buffer += char
                    self.refresh_pinyin_candidates()
                else:
                    self.search_query += char
            elif action == "delete":
                if self.keyboard_mode == "pinyin" and self.pinyin_buffer:
                    self.pinyin_buffer = self.pinyin_buffer[:-1]
                    self.refresh_pinyin_candidates()
                else:
                    self.search_query = self.search_query[:-1]
            elif action == "search":
                self.submit_search(from_keyboard=True)
            elif action == "space":
                if self.keyboard_mode == "pinyin":
                    self.commit_pinyin(add_space=True)
                else:
                    self.search_query += " "
            elif action == "back":
                self.back()
            elif action == "toggle_ime":
                if self.keyboard_mode == "pinyin":
                    self.commit_pinyin()
                    self.keyboard_mode = "direct"
                else:
                    self.keyboard_mode = "pinyin"
            return
        if action == "up":
            self.move(-1)
        elif action == "down":
            self.move(1)
        elif action == "left":
            self.switch_horizontal(-1)
        elif action == "right":
            self.switch_horizontal(1)
        elif action == "confirm":
            self.confirm()
        elif action == "back":
            self.back()
        elif action == "search":
            self.open_keyboard()
        elif action == "favorite" and self.page == "detail":
            self.toggle_favorite()
        elif action == "refresh" and self.page == "root" and self.tab == 0:
            self.start_job(
                "正在刷新热门目录……",
                self.catalog_client.popular,
                self.catalog_loaded,
            )

    def handle_controller_axis(self, event):
        deadzone = 18000
        if event.caxis.axis == SDL_CONTROLLER_AXIS_LEFTX:
            value = event.caxis.value
            direction = -1 if value < -deadzone else (1 if value > deadzone else 0)
            if direction and direction != self.axis_x:
                self.handle_action("left" if direction < 0 else "right")
            self.axis_x = direction
        elif event.caxis.axis == SDL_CONTROLLER_AXIS_LEFTY:
            value = event.caxis.value
            direction = -1 if value < -deadzone else (1 if value > deadzone else 0)
            if direction and direction != self.axis_y:
                self.handle_action("up" if direction < 0 else "down")
            self.axis_y = direction

    def handle_event(self, event):
        if event.type == SDL_QUIT:
            self.running = False
            return
        if event.type == SDL_CONTROLLERAXISMOTION:
            self.handle_controller_axis(event)
        elif event.type == SDL_CONTROLLERBUTTONDOWN:
            button = event.cbutton.button
            mapping = {
                SDL_CONTROLLER_BUTTON_DPAD_UP: "up",
                SDL_CONTROLLER_BUTTON_DPAD_DOWN: "down",
                SDL_CONTROLLER_BUTTON_DPAD_LEFT: "left",
                SDL_CONTROLLER_BUTTON_DPAD_RIGHT: "right",
                SDL_CONTROLLER_BUTTON_A: "confirm",
                SDL_CONTROLLER_BUTTON_B: "back",
                SDL_CONTROLLER_BUTTON_X: (
                    "delete" if self.page == "keyboard" else "favorite"
                ),
                SDL_CONTROLLER_BUTTON_Y: (
                    "space" if self.page == "keyboard" else "search"
                ),
                SDL_CONTROLLER_BUTTON_START: (
                    "search" if self.page == "keyboard" else "refresh"
                ),
                SDL_CONTROLLER_BUTTON_BACK: "toggle_ime",
                SDL_CONTROLLER_BUTTON_LEFTSHOULDER: (
                    "candidate_left" if self.page == "keyboard" else "left"
                ),
                SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: (
                    "candidate_right" if self.page == "keyboard" else "right"
                ),
            }
            action = mapping.get(button)
            if action:
                self.handle_action(action)
        elif event.type == SDL_JOYHATMOTION:
            value = event.jhat.value
            if value & SDL_HAT_UP:
                self.handle_action("up")
            elif value & SDL_HAT_DOWN:
                self.handle_action("down")
            elif value & SDL_HAT_LEFT:
                self.handle_action("left")
            elif value & SDL_HAT_RIGHT:
                self.handle_action("right")
        elif event.type == SDL_JOYBUTTONDOWN and not self.controller:
            if event.jbutton.button in (0, 3):
                self.handle_action("confirm")
            elif event.jbutton.button in (1, 4):
                self.handle_action("back")
        elif event.type == SDL_TEXTINPUT and self.page == "keyboard":
            raw = bytes(event.text.text).split(b"\0", 1)[0]
            text = raw.decode("utf-8", "ignore")
            if self.keyboard_mode == "pinyin":
                self.pinyin_buffer += text.lower()
                self.refresh_pinyin_candidates()
            else:
                self.search_query += text
        elif event.type == SDL_KEYDOWN:
            key = event.key.keysym.sym
            mapping = {
                SDLK_UP: "up",
                SDLK_w: "up",
                SDLK_DOWN: "down",
                SDLK_s: "down",
                SDLK_LEFT: "left",
                SDLK_a: "left",
                SDLK_RIGHT: "right",
                SDLK_d: "right",
                SDLK_RETURN: "confirm",
                SDLK_z: "confirm",
                SDLK_ESCAPE: "back",
                SDLK_x: "back",
                SDLK_BACKSPACE: "delete",
                SDLK_SPACE: "space",
                SDLK_F5: "refresh",
                SDLK_TAB: "toggle_ime",
            }
            action = mapping.get(key)
            if action:
                self.handle_action(action)

    def run(self):
        event = SDL_Event()
        while self.running:
            self.poll_jobs()
            while SDL_PollEvent(event):
                self.handle_event(event)
            self.render()
            SDL_Delay(16)

    def cleanup(self):
        SDL_StopTextInput()
        if self.controller:
            SDL_GameControllerClose(self.controller)
        if self.joystick:
            SDL_JoystickClose(self.joystick)
        for font in (self.font_title, self.font_body, self.font_small, self.font_tiny):
            if font:
                ttf.TTF_CloseFont(font)
        if getattr(self, "renderer", None):
            SDL_DestroyRenderer(self.renderer)
        if getattr(self, "window", None):
            SDL_DestroyWindow(self.window)
        ttf.TTF_Quit()
        SDL_Quit()


def main():
    app = None
    try:
        app = KazumiLiteApp()
        app.run()
        return 0
    except Exception as exc:
        print(f"FATAL: {exc}", flush=True)
        traceback.print_exc()
        return 1
    finally:
        if app:
            app.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
