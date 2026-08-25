#!/usr/bin/env python3
"""Standard-library network and persistence backend for Kazumi Lite."""

import gzip
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


USER_AGENT = "KazumiLite-muOS/0.2"
XIFAN_API = "https://rzmsnqblptbceicadbyd.supabase.co"
XIFAN_KEY = "sb_publishable_aCb7uwyLN6H-sMjze4dRGA_2MDuROLF"
AGE_BASE = "https://www.agedm.io"


class NetworkError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, timeout=18):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def request(self, url, method="GET", payload=None, headers=None):
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip",
        }
        request_headers.update(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            url, data=body, headers=request_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read(256).decode("utf-8", "replace")
            raise NetworkError(f"服务器错误 HTTP {exc.code}：{detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"网络连接失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise NetworkError("网络请求超时") from exc

    def json(self, url, method="GET", payload=None, headers=None):
        text = self.request(url, method=method, payload=payload, headers=headers)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise NetworkError("服务器返回了无法识别的数据") from exc


class CatalogClient:
    def __init__(self, http=None):
        self.http = http or HttpClient()

    def popular(self, limit=120, offset=0):
        query = urllib.parse.urlencode(
            {
                "select": "id,title,release_year,current_episodes,total_episodes,bangumi_score,view_count",
                "order": "view_count.desc",
                "limit": limit,
                "offset": offset,
            }
        )
        headers = {
            "apikey": XIFAN_KEY,
            "Authorization": f"Bearer {XIFAN_KEY}",
        }
        rows = self.http.json(
            f"{XIFAN_API}/rest/v1/animes?{query}", headers=headers
        )
        results = []
        for row in rows if isinstance(rows, list) else []:
            title = (row.get("title") or "").strip()
            if not title or row.get("id") is None:
                continue
            results.append(
                {
                    "id": int(row["id"]),
                    "title": title,
                    "subtitle": XifanSource.anime_subtitle(row),
                }
            )
        return results


class AgeSource:
    """Small HTML rule for AGE's currently working direct HLS routes.

    The AGE Kazumi rule hands a WebView an encrypted player page.  AGE also
    exposes the selected m3u8 URL in that page, so the muOS client can use
    requests + MPV without embedding a WebView.
    """

    name = "AGE动漫"
    _episode_re = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', re.I
    )
    _iframe_re = re.compile(
        r'<iframe[^>]+id=["\']iframeForVideo["\'][^>]+src=["\']([^"\']+)',
        re.I,
    )
    _vurl_re = re.compile(r"var\s+Vurl\s*=\s*[\"']([^\"']+)", re.I)
    _result_re = re.compile(
        r'<a[^>]+href=["\']([^"\']*/detail/[^"\']+)["\'][^>]+title=["\']([^"\']+)',
        re.I,
    )

    def __init__(self, http=None):
        self.http = http or HttpClient(timeout=20)

    @staticmethod
    def _url(value, base=AGE_BASE):
        result = urllib.parse.urljoin(base + "/", value)
        parsed = urllib.parse.urlsplit(result)
        if parsed.netloc.endswith("agedm.io"):
            result = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
        return result

    @staticmethod
    def _html(text):
        return re.sub(r"<[^>]+>", "", text or "").strip()

    def search(self, keyword, limit=30):
        url = f"{AGE_BASE}/search?query={urllib.parse.quote(keyword.strip())}"
        raw = self.http.request(url, headers={"Referer": AGE_BASE + "/"})
        rows = []
        seen = set()
        for href, title in self._result_re.findall(raw):
            title = self._html(title)
            href = self._url(href)
            if title and href not in seen:
                rows.append({"id": href, "title": title, "subtitle": "AGE动漫"})
                seen.add(href)
            if len(rows) >= limit:
                break
        return rows

    def detail(self, source_id):
        raw = self.http.request(self._url(source_id), headers={"Referer": AGE_BASE + "/"})
        title_match = re.search(r"<title[^>]*>(.*?)\s*-\s*AGE动漫</title>", raw, re.I | re.S)
        title = self._html(title_match.group(1)) if title_match else "AGE番剧"
        source_names = []
        for name in re.findall(
            r'<button[^>]+data-bs-target=["\']#playlist-source-[^"\']+["\'][^>]*>(.*?)</button>',
            raw,
            re.I | re.S,
        ):
            source_names.append(self._html(name))
        sources = []
        for road_index, name in enumerate(source_names, 1):
            marker = f'playlist-source-'
            # Links are grouped by tab panes; selecting /play/id/road/episode
            # is sufficient and avoids brittle DOM dependencies.
            episode_rows = []
            for href, label in re.findall(
                rf'<a[^>]+href=["\']([^"\']*/play/[^"\']*/{road_index}/\d+)["\'][^>]*>(.*?)</a>',
                raw,
                re.I | re.S,
            ):
                label = self._html(label)
                episode_rows.append(
                    {"id": self._url(href), "number": len(episode_rows) + 1, "title": label, "label": label}
                )
            if episode_rows:
                sources.append({"id": f"age-{road_index}", "code": str(road_index), "name": name or f"AGE线路{road_index}", "episodes": episode_rows})
        if not sources:
            raise NetworkError("AGE 没有找到可播放剧集")
        # The first AGE tab is a WebView-only VIP route. Put direct HLS routes
        # first so A/确认 on the handheld starts with a native-playable one.
        sources.sort(key=lambda item: ("VIP" in item["name"].upper(), item["name"]))
        return {"anime": {"id": source_id, "title": title, "release_year": "", "bangumi_score": 0}, "sources": sources}

    def playback(self, episode_id, max_height=480):
        play_url = self._url(episode_id)
        raw = self.http.request(play_url, headers={"Referer": AGE_BASE + "/"})
        iframe_match = self._iframe_re.search(raw)
        if not iframe_match:
            raise NetworkError("AGE 播放页没有找到播放器")
        iframe = urllib.parse.unquote(self._url(iframe_match.group(1), "https://jx.wuzhoupai.com:8443"))
        player = self.http.request(iframe, headers={"Referer": play_url})
        match = self._vurl_re.search(player)
        if not match:
            raise NetworkError("AGE 播放器没有返回直链（可能是 VIP 加密线路）")
        url = match.group(1)
        if ".m3u8" not in url.lower() and ".mp4" not in url.lower():
            raise NetworkError("AGE 返回了不可识别的媒体地址")
        return {"url": url, "fallback_url": "", "quality": "AGE HLS", "kind": "hls"}


class XifanSource:
    name = "稀饭动漫 Next"

    def __init__(self, http=None):
        self.http = http or HttpClient()
        self.api_headers = {
            "apikey": XIFAN_KEY,
            "Authorization": f"Bearer {XIFAN_KEY}",
        }

    def _rpc(self, name, payload):
        return self.http.json(
            f"{XIFAN_API}/rest/v1/rpc/{name}",
            method="POST",
            payload=payload,
            headers=self.api_headers,
        )

    def search(self, keyword, limit=30):
        keyword = keyword.strip()
        if not keyword:
            return []
        rows = self._rpc(
            "search_animes",
            {
                "search_term": keyword,
                "page_number": 1,
                "items_per_page": limit,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
        )
        results = []
        for row in rows if isinstance(rows, list) else []:
            title = (row.get("title") or "").strip()
            if title and row.get("id") is not None:
                results.append(
                    {
                        "id": int(row["id"]),
                        "title": title,
                        "subtitle": self.anime_subtitle(row),
                    }
                )
        return results

    @staticmethod
    def anime_subtitle(row):
        parts = []
        year = row.get("release_year")
        if year:
            parts.append(str(year))
        current = row.get("current_episodes")
        total = row.get("total_episodes")
        if current or total:
            parts.append(f"{current or '?'} / {total or '?'} 集")
        score = row.get("bangumi_score")
        if score:
            parts.append(f"评分 {score:g}")
        return "  ".join(parts) or "在线番剧"

    def detail(self, anime_id):
        payload = self._rpc("get_anime_detail", {"p_id": int(anime_id)})
        anime = payload.get("anime") or {}
        sources = []
        for source in payload.get("sources") or []:
            episodes = []
            for episode in source.get("episodes") or []:
                episode_id = episode.get("id")
                if episode_id is None:
                    continue
                number = episode.get("episode_number")
                title = (episode.get("title") or "").strip()
                episodes.append(
                    {
                        "id": int(episode_id),
                        "number": number,
                        "title": title,
                        "label": self.episode_label(number, title),
                    }
                )
            if episodes:
                sources.append(
                    {
                        "id": source.get("id"),
                        "code": source.get("code") or "",
                        "name": source.get("name") or "默认线路",
                        "episodes": episodes,
                    }
                )
        if not anime or not sources:
            raise NetworkError("该条目暂时没有可播放的剧集")
        anime.setdefault("id", int(anime_id))
        anime.setdefault("title", "未命名番剧")
        return {"anime": anime, "sources": sources}

    @staticmethod
    def episode_label(number, title):
        if number is None:
            prefix = "剧集"
        else:
            try:
                value = float(number)
                shown = str(int(value)) if value.is_integer() else f"{value:g}"
            except (TypeError, ValueError):
                shown = str(number)
            prefix = f"第 {shown} 集"
        return f"{prefix} · {title}" if title else prefix

    def playback(self, episode_id, max_height=480):
        headers = dict(self.api_headers)
        headers["x-client-info"] = USER_AGENT
        endpoint = f"{XIFAN_API}/functions/v1/issue-web-playback"
        hls = self.http.json(
            endpoint,
            method="POST",
            payload={"action": "hls", "episode_id": int(episode_id)},
            headers=headers,
        )
        fallback_url = ""
        try:
            fallback = self.http.json(
                endpoint,
                method="POST",
                payload={"action": "fallback", "episode_id": int(episode_id)},
                headers=headers,
            )
            if fallback.get("ok"):
                fallback_url = fallback.get("url") or ""
        except NetworkError:
            pass

        if hls.get("ok"):
            url, quality = select_hls_variant(
                hls.get("master_playlist") or "",
                hls.get("url") or "",
                max_height,
            )
            if url:
                return {
                    "url": url,
                    "fallback_url": fallback_url,
                    "quality": quality,
                    "kind": "hls",
                }
        if fallback_url:
            return {
                "url": fallback_url,
                "fallback_url": "",
                "quality": "MP4 直连",
                "kind": "mp4",
            }
        error = hls.get("error") or "unknown"
        raise NetworkError(f"暂时无法取得播放地址：{error}")


def select_hls_variant(master_playlist, fallback_url, max_height=480):
    variants = []
    pending_height = None
    for raw_line in master_playlist.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            match = re.search(r"RESOLUTION=\d+x(\d+)", line, re.I)
            pending_height = int(match.group(1)) if match else 0
        elif pending_height is not None and line and not line.startswith("#"):
            variants.append((pending_height, line))
            pending_height = None
    if not variants:
        return fallback_url, "自动"
    within_limit = [item for item in variants if 0 < item[0] <= max_height]
    if within_limit:
        height, url = max(within_limit, key=lambda item: item[0])
    else:
        height, url = min(variants, key=lambda item: item[0] or 99999)
    return url, f"{height}p" if height else "自动"


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
