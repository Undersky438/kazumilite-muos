"""Small standard-library HTTP client used by external sources."""

import gzip
import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from config import APP_VERSION


USER_AGENT = f"KazumiLite-muOS/{APP_VERSION}"


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
