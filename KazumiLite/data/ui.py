"""SDL rendering for Kazumi Lite's 640x480 interface."""

import ctypes

import sdl2.sdlttf as ttf
from sdl2 import *

from config import APP_VERSION, KEYBOARD, Palette, format_time


class RenderMixin:
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
