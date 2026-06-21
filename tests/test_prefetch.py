"""Tests for background page prefetching."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dlsite_opds.core.auth import SourceImageLRU
from dlsite_opds.routes.pages import prefetch_pages
from dlsite_opds.services.image_cache import ImageCache

from .conftest import FakePlayFile, make_jpeg


@pytest.fixture()
def app_state(tmp_path):
    cache = ImageCache(tmp_path / "cache", ttl=86400)
    return SimpleNamespace(
        image_cache=cache,
        source_cache=SourceImageLRU(),
        image_executor=ThreadPoolExecutor(max_workers=2),
        prefetch_inflight=set(),
    )


@pytest.fixture()
def mock_client():
    client = MagicMock()
    client.get_cached_page_count.return_value = 10
    jpeg_bytes = make_jpeg()
    pf = FakePlayFile()
    client.download_page_image = AsyncMock(return_value=(jpeg_bytes, pf))
    return client


class TestPrefetchPages:
    @pytest.mark.asyncio
    async def test_prefetch_populates_disk_cache(
        self, app_state, mock_client
    ) -> None:
        await prefetch_pages(
            app_state, mock_client,
            product_id="RJ123456", start_page=1, max_width=800, count=3,
        )

        cache: ImageCache = app_state.image_cache
        for pg in range(1, 4):
            assert cache.get("RJ123456", pg, 800) is not None

    @pytest.mark.asyncio
    async def test_prefetch_skips_cached_pages(
        self, app_state, mock_client
    ) -> None:
        cache: ImageCache = app_state.image_cache
        cache.put("RJ123456", 2, 800, b"existing")

        await prefetch_pages(
            app_state, mock_client,
            product_id="RJ123456", start_page=1, max_width=800, count=3,
        )

        assert mock_client.download_page_image.call_count == 2
        assert cache.get("RJ123456", 2, 800) == b"existing"

    @pytest.mark.asyncio
    async def test_prefetch_respects_page_count_limit(
        self, app_state, mock_client
    ) -> None:
        mock_client.get_cached_page_count.return_value = 3

        await prefetch_pages(
            app_state, mock_client,
            product_id="RJ123456", start_page=1, max_width=800, count=5,
        )

        assert mock_client.download_page_image.call_count == 2

    @pytest.mark.asyncio
    async def test_prefetch_skips_inflight_pages(
        self, app_state, mock_client
    ) -> None:
        app_state.prefetch_inflight.add(("RJ123456", "", 2, 800))

        await prefetch_pages(
            app_state, mock_client,
            product_id="RJ123456", start_page=1, max_width=800, count=3,
        )

        assert mock_client.download_page_image.call_count == 2

    @pytest.mark.asyncio
    async def test_prefetch_clears_inflight_on_completion(
        self, app_state, mock_client
    ) -> None:
        await prefetch_pages(
            app_state, mock_client,
            product_id="RJ123456", start_page=1, max_width=800, count=2,
        )

        assert len(app_state.prefetch_inflight) == 0

    @pytest.mark.asyncio
    async def test_prefetch_clears_inflight_on_failure(
        self, app_state, mock_client
    ) -> None:
        mock_client.download_page_image = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        await prefetch_pages(
            app_state, mock_client,
            product_id="RJ123456", start_page=1, max_width=800, count=2,
        )

        assert len(app_state.prefetch_inflight) == 0
