"""Atomic persistence for favorites, history, searches, and catalog cache."""

import json
import os
import tempfile
import time


class StateStore:
    def __init__(self, path):
        self.path = path
        self.data = {
            "favorites": [],
            "history": [],
            "queries": [],
            "catalog": [],
        }
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                for key in self.data:
                    if isinstance(payload.get(key), list):
                        self.data[key] = payload[key]
        except (OSError, ValueError):
            pass

    def save(self):
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="state-", suffix=".tmp", dir=parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def set_catalog(self, items):
        self.data["catalog"] = list(items)[:120]
        self.save()

    def is_favorite(self, anime_id):
        return any(
            str(item.get("id", "")) == str(anime_id)
            for item in self.data["favorites"]
        )

    def toggle_favorite(self, anime_id, title, subtitle=""):
        items = self.data["favorites"]
        for index, item in enumerate(items):
            if str(item.get("id", "")) == str(anime_id):
                del items[index]
                self.save()
                return False
        items.insert(0, {"id": anime_id, "title": title, "subtitle": subtitle})
        del items[100:]
        self.save()
        return True

    def add_query(self, query):
        query = query.strip()
        if not query:
            return
        queries = [item for item in self.data["queries"] if item != query]
        queries.insert(0, query)
        self.data["queries"] = queries[:12]
        self.save()

    def playback_position(self, anime_id, episode_id):
        for item in self.data["history"]:
            if str(item.get("anime_id", "")) == str(anime_id) and str(
                item.get("episode_id", "")
            ) == str(episode_id):
                position = float(item.get("position") or 0)
                duration = float(item.get("duration") or 0)
                if duration > 0 and position / duration >= 0.92:
                    return 0.0
                return position
        return 0.0

    def record_playback(
        self,
        anime_id,
        anime_title,
        episode_id,
        episode_label,
        position,
        duration,
    ):
        history = [
            item
            for item in self.data["history"]
            if str(item.get("anime_id", "")) != str(anime_id)
        ]
        history.insert(
            0,
            {
                "anime_id": anime_id,
                "title": anime_title,
                "episode_id": episode_id,
                "episode": episode_label,
                "position": round(float(position or 0), 1),
                "duration": round(float(duration or 0), 1),
                "updated": int(time.time()),
            },
        )
        self.data["history"] = history[:100]
        self.save()
