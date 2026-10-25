"""BigKinds OpenAPI client: one call at a time, disk cache, bounded retries.

The access key travels only in the request body. Cache keys and cache files are
derived from the argument alone, so the key never reaches disk.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

import requests

BASE_URL = "https://tools.kinds.or.kr"
RETRY_WAITS = (2, 5, 15)

Transport = Callable[[str, bytes], tuple[int, bytes]]


class BigKindsError(RuntimeError):
    def __init__(self, result, reason: str):
        super().__init__(f"BigKinds error result={result}: {reason}")
        self.result = result
        self.reason = reason


def _requests_transport(path: str, body: bytes) -> tuple[int, bytes]:
    resp = requests.post(BASE_URL + path, data=body,
                         headers={"Content-Type": "application/json; charset=utf-8"}, timeout=120)
    return resp.status_code, resp.content


class BigKindsClient:
    def __init__(self, key: str, cache_dir: Path, transport: Transport | None = None,
                 min_interval: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._key = key
        self._cache_dir = Path(cache_dir)
        self._transport = transport or _requests_transport
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def post(self, path: str, argument: dict, *, cache: bool) -> dict:
        cache_file = self._cache_file(path, argument)
        if cache and cache_file.is_file():
            return json.loads(cache_file.read_text(encoding="utf-8"))["return_object"]

        payload = self._request(path, argument)
        if cache:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload["return_object"]

    def _cache_file(self, path: str, argument: dict) -> Path:
        digest = hashlib.sha256(json.dumps(argument, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return self._cache_dir / path.strip("/").replace("/", "_") / f"{digest}.json"

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_call is not None:
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                self._sleep(wait)
                now += wait
        self._last_call = now

    def _request(self, path: str, argument: dict) -> dict:
        body = json.dumps({"access_key": self._key, "argument": argument}, ensure_ascii=False).encode("utf-8")
        self._throttle()
        failure = ""
        for attempt in range(len(RETRY_WAITS) + 1):
            if attempt:
                self._sleep(RETRY_WAITS[attempt - 1])
            try:
                status, raw = self._transport(path, body)
            except (requests.RequestException, OSError) as exc:
                failure = f"{type(exc).__name__}: {exc}"
                continue
            if status >= 500:
                failure = f"HTTP {status}"
                continue
            if status != 200:
                raise BigKindsError(None, f"HTTP {status}")
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("result") != 0:
                raise BigKindsError(payload.get("result"), str(payload.get("reason", "")))
            return payload
        raise BigKindsError(None, f"gave up after {len(RETRY_WAITS)} retries: {failure}")


class CacheOnlyClient:
    """Answers from the disk cache only and never calls the network — for re-reading searches already paid for."""

    def __init__(self, client: BigKindsClient):
        self.client = client
        self.misses = 0

    def post(self, path: str, argument: dict, *, cache: bool) -> dict:
        cache_file = self.client._cache_file(path, argument)
        if not cache_file.is_file():
            self.misses += 1
            return {"documents": []}
        return json.loads(cache_file.read_text(encoding="utf-8"))["return_object"]
