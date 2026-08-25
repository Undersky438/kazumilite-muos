#!/usr/bin/env python3
"""Controller-first Kazumi client for muOS Jacaranda."""

import os
import queue
import threading
import time
import traceback

from backend import AgeSource, CatalogClient, StateStore, XifanSource
from config import APP_VERSION, FONT_PATH, KEYBOARD, STATE_PATH, env_int, format_time
from ime import PinyinMixin

try:
    import sdl2
    import sdl2.sdlttf as ttf
    from sdl2 import *
except Exception as exc:
    print(f"PySDL2 import failed: {exc}", flush=True)
    raise

from player import PlayerMixin
from ui import RenderMixin


class KazumiLiteApp(PinyinMixin, RenderMixin, PlayerMixin):
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
        self.window = None
        self.renderer = None
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
        if SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER) != 0:
            raise RuntimeError(self.sdl_error("SDL 初始化失败"))
        if ttf.TTF_Init() != 0:
            raise RuntimeError(self.sdl_error("SDL_ttf 初始化失败"))
        self.create_video_output()

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

        self.start_job(
            "正在更新热门番剧……",
            self.catalog_client.popular,
            self.catalog_loaded,
        )

    def create_video_output(self):
        """Create the SDL video resources used by the application UI."""
        if SDL_InitSubSystem(SDL_INIT_VIDEO) != 0:
            raise RuntimeError(self.sdl_error("SDL 视频子系统初始化失败"))
        window = SDL_CreateWindow(
            "Kazumi Lite".encode("utf-8"),
            SDL_WINDOWPOS_CENTERED,
            SDL_WINDOWPOS_CENTERED,
            self.width,
            self.height,
            SDL_WINDOW_SHOWN,
        )
        if not window:
            SDL_QuitSubSystem(SDL_INIT_VIDEO)
            raise RuntimeError(self.sdl_error("窗口创建失败"))
        flags = SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC
        renderer = SDL_CreateRenderer(window, -1, flags)
        if not renderer:
            renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE)
        if not renderer:
            SDL_DestroyWindow(window)
            SDL_QuitSubSystem(SDL_INIT_VIDEO)
            raise RuntimeError(self.sdl_error("渲染器创建失败"))
        self.window = window
        self.renderer = renderer
        SDL_SetRenderDrawBlendMode(renderer, SDL_BLENDMODE_BLEND)
        SDL_StartTextInput()

    def release_video_output(self):
        """Release KMS/DRM ownership before starting an external player."""
        SDL_StopTextInput()
        if self.renderer:
            SDL_DestroyRenderer(self.renderer)
            self.renderer = None
        if self.window:
            SDL_DestroyWindow(self.window)
            self.window = None
        SDL_QuitSubSystem(SDL_INIT_VIDEO)
        print("[display] SDL video released", flush=True)

    def restore_video_output(self):
        """Reclaim the display after MPV has closed and replace stale buffers."""
        SDL_Delay(120)
        self.create_video_output()
        driver = SDL_GetCurrentVideoDriver()
        driver_name = driver.decode("utf-8", "replace") if driver else "unknown"
        print(f"[display] SDL video restored: {driver_name}", flush=True)
        SDL_FlushEvents(SDL_FIRSTEVENT, SDL_LASTEVENT)
        for _ in range(12):
            self.render()
            SDL_PumpEvents()
            SDL_Delay(18)

    def px(self, value):
        return max(1, int(round(value * self.scale)))

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
        if self.window:
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
