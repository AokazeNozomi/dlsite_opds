"""Tests for OPDS-PSE page route behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dlsite_opds.core.auth import AuthContext, get_auth
from dlsite_opds.routes import pages
from dlsite_opds.services.chapters import ChapterGroup
from tests.conftest import FakePlayFile, make_jpeg


def _make_app(client: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(pages.router)
    app.state.image_cache = MagicMock()
    app.state.image_cache.get.return_value = None
    app.state.source_cache = MagicMock()
    app.state.source_cache.get.return_value = None
    app.state.image_executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(
        max_workers=1
    )
    app.state.settings = MagicMock(prefetch_ahead=0)
    app.state.prefetch_inflight = set()

    async def _auth() -> AuthContext:
        return AuthContext(client=client, progress=MagicMock())

    app.dependency_overrides[get_auth] = _auth
    return app


@pytest.mark.asyncio
async def test_pse_allows_multi_chapter_without_chapter_param() -> None:
    pf = FakePlayFile()
    chapters = [
        ChapterGroup(key="img:root", title="root", pages=[("001.jpg", pf)]),
        ChapterGroup(
            key="img:chapter1",
            title="chapter1",
            pages=[("chapter1/001.jpg", pf)],
        ),
    ]
    flat_pages = chapters[0].pages + chapters[1].pages
    data = MagicMock()
    data.chapters = chapters
    data.pages = flat_pages
    data.pages_for_chapter.side_effect = lambda key: (
        flat_pages if key is None else next(
            ch.pages for ch in chapters if ch.key == key
        )
    )

    client = AsyncMock()
    client.ensure_valid_token = AsyncMock(return_value=data)
    client.download_page_image = AsyncMock(return_value=(make_jpeg(), pf))

    app = _make_app(client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/pse/RJ000001", params={"page": 0})

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    client.download_page_image.assert_awaited_once_with(
        "RJ000001", 0, chapter_key=None
    )
    app.state.image_executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_pse_returns_502_on_image_processing_failure() -> None:
    pf = FakePlayFile()
    data = MagicMock()
    data.chapters = []
    data.pages = [("001.jpg", pf)]
    data.pages_for_chapter.return_value = data.pages

    client = AsyncMock()
    client.ensure_valid_token = AsyncMock(return_value=data)
    client.download_page_image = AsyncMock(return_value=(b"not-a-jpeg", pf))

    app = _make_app(client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/pse/RJ000001", params={"page": 0})

    assert r.status_code == 502
    app.state.image_executor.shutdown(wait=False)
