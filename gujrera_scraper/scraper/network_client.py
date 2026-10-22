"""HTTP client for the public GujRERA APIs.

GujRERA's server only supports legacy TLS renegotiation, which OpenSSL 3 rejects by
default (UNSAFE_LEGACY_RENEGOTIATION_DISABLED). We opt in with OP_LEGACY_SERVER_CONNECT.
This only relaxes the renegotiation check; certificate verification stays on.

Every response is persisted (gzip) into `raw_responses` so parsers can be re-run
without re-crawling and every parsed value can be traced back to the exact payload.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://gujrera.gujarat.gov.in/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; GujRERA-public-research-bot/1.0; "
    "throttled; public endpoints only)"
)
OP_LEGACY_SERVER_CONNECT = 0x4


def legacy_tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.options |= OP_LEGACY_SERVER_CONNECT
    return ctx


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, message: str, network: bool = False):
        super().__init__(f"{url}: {status} {message}")
        self.url = url
        self.status = status
        # True when the request never reached the server (no internet, DNS failure, timeout):
        # the crawler pauses and retries instead of counting it as a failed task.
        self.network = network


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    content_type: str
    sha256: str
    elapsed: float
    changed: bool  # body differs from the last stored copy of this URL

    def json(self) -> Any:
        return json.loads(self.content)


class RateLimiter:
    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.min_interval


class GujReraClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        raw_store=None,
        rate_per_sec: float = 2.0,
        max_retries: int = 5,
        timeout: float = 120.0,
        backoff_base: float = 2.0,
    ):
        self.raw_store = raw_store
        self.limiter = RateLimiter(rate_per_sec)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self._client = self._new_client()
        self.stats = {"requests": 0, "retries": 0, "errors": 0, "bytes": 0}

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            verify=legacy_tls_context(),
            timeout=httpx.Timeout(self.timeout, connect=30.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, path: str, *, store: bool = True, binary: bool = False, max_retries: int | None = None) -> Response:
        return self._request("GET", path, None, store=store, binary=binary, max_retries=max_retries)

    def post_json(self, path: str, body: dict, *, store: bool = True, max_retries: int | None = None) -> Response:
        """POST a JSON body to a public read endpoint (e.g. the unit list). Same retry/backoff/rate limit as get().
        `Response.url` is `BASE_URL + path + '#' + <body as query string>`: raw_responses.url must be unique per
        request and the fragment is never sent to the server."""
        return self._request("POST", path, body, store=store, binary=False, max_retries=max_retries)

    def _request(self, method: str, path: str, body: dict | None, *, store: bool, binary: bool,
                 max_retries: int | None = None) -> Response:
        path = path.lstrip("/")
        url = BASE_URL + path
        if body is not None:
            url += "#" + "&".join(f"{k}={body[k]}" for k in sorted(body))
        last_exc: Exception | None = None
        retries = self.max_retries if max_retries is None else max_retries
        for attempt in range(retries + 1):
            self.limiter.wait()
            t0 = time.monotonic()
            try:
                r = self._client.get(path) if method == "GET" else self._client.post(path, json=body)
                self.stats["requests"] += 1
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                self._sleep_backoff(attempt, url, repr(e), retries=retries)
                if isinstance(e, httpx.TransportError):
                    # connection may be poisoned; rebuild it
                    self._client.close()
                    self._client = self._new_client()
                continue
            elapsed = time.monotonic() - t0
            if r.status_code in self.RETRY_STATUSES:
                last_exc = FetchError(url, r.status_code, "retryable status")
                retry_after = r.headers.get("Retry-After")
                self._sleep_backoff(attempt, url, f"HTTP {r.status_code}",
                                    float(retry_after) if retry_after and retry_after.isdigit() else None,
                                    retries=retries)
                continue
            if r.status_code >= 400:
                self.stats["errors"] += 1
                raise FetchError(url, r.status_code, r.text[:200])
            content = r.content
            self.stats["bytes"] += len(content)
            sha = hashlib.sha256(content).hexdigest()
            changed = True
            if store and self.raw_store is not None:
                changed = self.raw_store.save_raw(
                    url=url, status=r.status_code,
                    content_type=r.headers.get("content-type", ""),
                    body=None if binary else content, sha256=sha,
                    size=len(content),
                )
            return Response(url, r.status_code, content, r.headers.get("content-type", ""),
                            sha, elapsed, changed)
        self.stats["errors"] += 1
        network = isinstance(last_exc, (httpx.TimeoutException, httpx.TransportError))
        last_status = last_exc.status if isinstance(last_exc, FetchError) else None   # e.g. a persistent HTTP 500
        raise FetchError(url, last_status, f"gave up after {retries} retries: {last_exc!r}", network=network)

    def get_json(self, path: str, **kw) -> Any:
        resp = self.get(path, **kw)
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise FetchError(resp.url, resp.status, f"invalid JSON: {e}") from e

    def _sleep_backoff(self, attempt: int, url: str, why: str, retry_after: float | None = None,
                       retries: int | None = None):
        self.stats["retries"] += 1
        if attempt >= (self.max_retries if retries is None else retries):
            return
        delay = retry_after if retry_after else min(self.backoff_base ** attempt, 120)
        log.warning("retry %d for %s (%s); sleeping %.1fs", attempt + 1, url, why, delay)
        time.sleep(delay)


def gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=6)


def gunzip(data: bytes) -> bytes:
    return gzip.decompress(data)
