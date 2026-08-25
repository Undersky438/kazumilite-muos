"""Controller-focused pinyin input support."""

from config import PINYIN_PATH


class PinyinMixin:
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
