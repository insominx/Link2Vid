"""Thumbnail loader with caching and background processing."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable
import hashlib
import io
import tempfile
import threading

import requests
from PIL import Image, ImageOps

ThumbnailCallback = Callable[[Image.Image | None], None]


class LruCache:
    def __init__(self, max_items: int = 120) -> None:
        self.max_items = max_items
        self._items: OrderedDict[str, Image.Image] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Image.Image | None:
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def set(self, key: str, value: Image.Image) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class ThumbnailLoader:
    def __init__(
        self,
        executor,
        cache_dir: str | Path | None = None,
        cache_max_items: int = 120,
        max_bytes: int = 5 * 1024 * 1024,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.executor = executor
        self.cache = LruCache(max_items=cache_max_items)
        self.max_bytes = max_bytes
        self.log = log
        if cache_dir is None:
            cache_dir = Path(tempfile.gettempdir()) / "link2vid_thumbnails"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._inflight: dict[str, list[ThumbnailCallback]] = {}
        self._lock = threading.Lock()

    def get_cached(self, url: str, size: tuple[int, int]) -> Image.Image | None:
        key = self._make_key(url, size)
        return self.cache.get(key)

    def submit(self, url: str, size: tuple[int, int], on_ready: ThumbnailCallback) -> None:
        key = self._make_key(url, size)
        cached = self.cache.get(key)
        if cached is not None:
            on_ready(cached)
            return

        with self._lock:
            if key in self._inflight:
                self._inflight[key].append(on_ready)
                return
            self._inflight[key] = [on_ready]

        def worker() -> None:
            image = self._load_thumbnail(url, size)
            if image is not None:
                self.cache.set(key, image)
            callbacks: list[ThumbnailCallback]
            with self._lock:
                callbacks = self._inflight.pop(key, [])
            for callback in callbacks:
                try:
                    callback(image)
                except Exception as exc:
                    self._log(f"Thumbnail callback failed: {exc}")

        self.executor.submit(worker)

    def _make_key(self, url: str, size: tuple[int, int]) -> str:
        return f"{url}|{size[0]}x{size[1]}"

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.png"

    def _load_thumbnail(self, url: str, size: tuple[int, int]) -> Image.Image | None:
        key = self._make_key(url, size)
        path = self._cache_path(key)
        image = self._load_from_disk(path)
        if image is not None:
            return image
        try:
            data = self._download_bytes(url)
            image = self._decode_image(data, size)
            if image is not None:
                self._save_to_disk(path, image)
            return image
        except Exception as exc:
            self._log(f"Thumbnail load failed: {exc}")
            return None

    def _load_from_disk(self, path: Path) -> Image.Image | None:
        if not path.exists():
            return None
        try:
            with Image.open(path) as img:
                return img.convert("RGB").copy()
        except Exception as exc:
            self._log(f"Thumbnail cache read failed: {exc}")
            return None

    def _download_bytes(self, url: str) -> bytes:
        response = requests.get(url, timeout=(5, 10), stream=True)
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            raise ValueError("Thumbnail too large")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > self.max_bytes:
                raise ValueError("Thumbnail too large")
        return bytes(data)

    def _decode_image(self, data: bytes, size: tuple[int, int]) -> Image.Image | None:
        try:
            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGB")
                resized = ImageOps.fit(img, size, method=Image.LANCZOS)
                return resized.copy()
        except Exception as exc:
            self._log(f"Thumbnail decode failed: {exc}")
            return None

    def _save_to_disk(self, path: Path, image: Image.Image) -> None:
        try:
            image.save(path, format="PNG")
        except Exception as exc:
            self._log(f"Thumbnail cache write failed: {exc}")

    def clear_cache(self, remove_dir: bool = False) -> None:
        self.cache.clear()
        if not self.cache_dir.exists():
            return
        try:
            for path in self.cache_dir.glob("*.png"):
                try:
                    path.unlink()
                except Exception as exc:
                    self._log(f"Thumbnail cache delete failed: {exc}")
            if remove_dir:
                try:
                    self.cache_dir.rmdir()
                except Exception as exc:
                    self._log(f"Thumbnail cache dir remove failed: {exc}")
        except Exception as exc:
            self._log(f"Thumbnail cache cleanup failed: {exc}")

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)
