import sys
import tempfile
import unittest
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "KazumiLite" / "data"
sys.path.insert(0, str(DATA_DIR))

from sources import XifanSource, select_hls_variant
from state_store import StateStore


class HlsSelectionTests(unittest.TestCase):
    def test_prefers_highest_variant_within_screen_limit(self):
        playlist = """#EXTM3U
#EXT-X-STREAM-INF:RESOLUTION=640x360
https://example.test/360.m3u8
#EXT-X-STREAM-INF:RESOLUTION=854x480
https://example.test/480.m3u8
#EXT-X-STREAM-INF:RESOLUTION=1280x720
https://example.test/720.m3u8
"""

        self.assertEqual(
            select_hls_variant(playlist, "fallback", 480),
            ("https://example.test/480.m3u8", "480p"),
        )

    def test_uses_fallback_when_playlist_has_no_variants(self):
        self.assertEqual(select_hls_variant("#EXTM3U", "fallback", 480), ("fallback", "自动"))


class StateStoreTests(unittest.TestCase):
    def test_round_trip_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(str(path))
            store.toggle_favorite(12, "测试番剧", "2026")
            store.add_query("frieren")
            store.record_playback(12, "测试番剧", 3, "第 3 集", 42.5, 1200)

            restored = StateStore(str(path))

            self.assertTrue(restored.is_favorite(12))
            self.assertEqual(restored.data["queries"], ["frieren"])
            self.assertEqual(restored.playback_position(12, 3), 42.5)

    def test_completed_episode_restarts_from_beginning(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(str(Path(directory) / "state.json"))
            store.record_playback(1, "测试", 2, "第 2 集", 95, 100)

            self.assertEqual(store.playback_position(1, 2), 0.0)


class LabelTests(unittest.TestCase):
    def test_episode_label_formats_whole_numbers(self):
        self.assertEqual(XifanSource.episode_label(3.0, "终章"), "第 3 集 · 终章")


if __name__ == "__main__":
    unittest.main()
