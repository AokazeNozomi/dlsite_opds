"""Filesystem-backed image cache with TTL-based expiry."""

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ImageCache:
    """Cache processed JPEG images on disk, keyed by (product_id, chapter, page, width).

    Files are stored as flat ``<sha256>.jpg`` entries.  Freshness is
    determined by comparing each file's mtime against the configured TTL.
    Writes are atomic (temp-file + rename) to avoid serving partial data.
    """

    def __init__(self, cache_dir: Path, ttl: int) -> None:
        self._dir = cache_dir
        self._ttl = ttl
        self._dir.mkdir(parents=True, exist_ok=True)

    def _key_path(
        self,
        product_id: str,
        page: int,
        width: int | None,
        chapter: str | None = None,
    ) -> Path:
        raw = f"{product_id}:{chapter or ''}:{page}:{width or 'full'}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return self._dir / f"{digest}.jpg"

    def get(
        self,
        product_id: str,
        page: int,
        width: int | None,
        chapter: str | None = None,
    ) -> bytes | None:
        path = self._key_path(product_id, page, width, chapter)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None

        if time.time() - stat.st_mtime > self._ttl:
            path.unlink(missing_ok=True)
            return None

        try:
            return path.read_bytes()
        except OSError:
            return None

    def put(
        self,
        product_id: str,
        page: int,
        width: int | None,
        data: bytes,
        chapter: str | None = None,
    ) -> None:
        path = self._key_path(product_id, page, width, chapter)
        try:
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Failed to write image cache entry %s: %s", path, exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _cover_path(self, product_id: str) -> Path:
        digest = hashlib.sha256(f"cover:{product_id}".encode()).hexdigest()
        return self._dir / f"{digest}.cover"

    def _cover_meta_path(self, product_id: str) -> Path:
        return self._cover_path(product_id).with_suffix(".cover.meta")

    def get_cover(self, product_id: str) -> tuple[bytes, str] | None:
        path = self._cover_path(product_id)
        meta_path = self._cover_meta_path(product_id)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None

        if time.time() - stat.st_mtime > self._ttl:
            path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return None

        try:
            body = path.read_bytes()
            content_type = meta_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return body, content_type or "image/jpeg"

    def put_cover(self, product_id: str, data: bytes, content_type: str) -> None:
        path = self._cover_path(product_id)
        meta_path = self._cover_meta_path(product_id)
        try:
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(tmp, path)
            meta_path.write_text(content_type, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write cover cache entry %s: %s", path, exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def evict_expired(self) -> int:
        """Remove all expired cache files.  Returns the number removed."""
        now = time.time()
        removed = 0
        try:
            for entry in self._dir.iterdir():
                if not entry.suffix == ".jpg":
                    continue
                try:
                    if now - entry.stat().st_mtime > self._ttl:
                        entry.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    continue
        except OSError:
            pass
        if removed:
            logger.info("Evicted %d expired image cache entries", removed)
        return removed
