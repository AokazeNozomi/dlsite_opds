"""Tests for the filesystem-backed ImageCache."""

import time

from dlsite_opds.services.image_cache import ImageCache


class TestImageCache:
    def test_get_returns_none_on_miss(self, tmp_path) -> None:
        cache = ImageCache(tmp_path / "cache", ttl=3600)
        assert cache.get("RJ001", 0, None) is None

    def test_put_then_get_roundtrip(self, tmp_path) -> None:
        cache = ImageCache(tmp_path / "cache", ttl=3600)
        data = b"\xff\xd8fake-jpeg-data"
        cache.put("RJ001", 5, 800, data)
        assert cache.get("RJ001", 5, 800) == data

    def test_different_widths_are_separate_entries(self, tmp_path) -> None:
        cache = ImageCache(tmp_path / "cache", ttl=3600)
        cache.put("RJ001", 0, None, b"full")
        cache.put("RJ001", 0, 800, b"w800")
        assert cache.get("RJ001", 0, None) == b"full"
        assert cache.get("RJ001", 0, 800) == b"w800"

    def test_expired_entry_returns_none(self, tmp_path) -> None:
        cache = ImageCache(tmp_path / "cache", ttl=1)
        cache.put("RJ001", 0, None, b"data")

        path = cache._key_path("RJ001", 0, None)
        import os
        old = time.time() - 10
        os.utime(path, (old, old))

        assert cache.get("RJ001", 0, None) is None
        assert not path.exists()

    def test_evict_expired_removes_stale_files(self, tmp_path) -> None:
        cache = ImageCache(tmp_path / "cache", ttl=1)
        cache.put("RJ001", 0, None, b"a")
        cache.put("RJ002", 1, 600, b"b")

        import os
        old = time.time() - 10
        for p in (tmp_path / "cache").iterdir():
            os.utime(p, (old, old))

        cache.put("RJ003", 2, None, b"c")

        removed = cache.evict_expired()
        assert removed == 2
        assert cache.get("RJ003", 2, None) == b"c"

    def test_put_overwrites_existing(self, tmp_path) -> None:
        cache = ImageCache(tmp_path / "cache", ttl=3600)
        cache.put("RJ001", 0, None, b"v1")
        cache.put("RJ001", 0, None, b"v2")
        assert cache.get("RJ001", 0, None) == b"v2"

    def test_cache_dir_created_automatically(self, tmp_path) -> None:
        cache_dir = tmp_path / "nested" / "deep" / "cache"
        cache = ImageCache(cache_dir, ttl=3600)
        cache.put("RJ001", 0, None, b"data")
        assert cache.get("RJ001", 0, None) == b"data"
