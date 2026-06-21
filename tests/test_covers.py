"""Tests for cover image proxy resilience."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dlsite_opds.core.auth import AuthContext, get_auth
from dlsite_opds.core.config import Settings
from dlsite_opds.core.http_utils import CATALOG_IMAGE_HEADERS
from dlsite_opds.routes import covers
from dlsite_opds.services.image_cache import ImageCache
from tests.conftest import make_jpeg


class _FakeCoverResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "image/jpeg") -> None:
        self.status = status
        self._body = body
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._body


class _FakeCoverSession:
    def __init__(self, responses: list[_FakeCoverResponse]) -> None:
        self._responses = responses
        self.calls = 0
        self.max_inflight = 0
        self._inflight = 0

    def get(self, url: str, headers: dict[str, str] | None = None) -> object:
        assert headers == CATALOG_IMAGE_HEADERS
        self.calls += 1
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        response = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        session = self

        class _Ctx:
            async def __aenter__(_self) -> _FakeCoverResponse:
                await asyncio.sleep(0.02)
                return response

            async def __aexit__(_self, *args: object) -> None:
                session._inflight -= 1

        return _Ctx()


def _make_work(product_id: str = "RJ000001") -> MagicMock:
    work = MagicMock()
    work.product_id = product_id
    work.work_image = "//img.dlsite.jp/modpub/images2/work/doujin/RJ000001.jpg"
    return work


def _make_app(
    session: _FakeCoverSession,
    disk_cache: ImageCache,
    *,
    concurrency: int = 2,
    retries: int = 3,
) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(covers.router)
    app.state.cover_cache = {}
    app.state.image_cache = disk_cache
    app.state.cover_session = session  # type: ignore[assignment]
    app.state.cover_semaphore = asyncio.Semaphore(concurrency)
    app.state.settings = Settings(
        cover_concurrency=concurrency,
        cover_fetch_retries=retries,
        cover_retry_delay=0.0,
    )

    client = AsyncMock()
    work = _make_work()
    client.get_purchases = AsyncMock(return_value=[(work, None)])

    async def _auth() -> AuthContext:
        return AuthContext(client=client, progress=MagicMock())

    app.dependency_overrides[get_auth] = _auth
    return app, client


@pytest.mark.asyncio
async def test_cover_retries_then_succeeds(tmp_path) -> None:
    body = make_jpeg(120, 160)
    session = _FakeCoverSession(
        [
            _FakeCoverResponse(503, b""),
            _FakeCoverResponse(200, body),
        ]
    )
    disk_cache = ImageCache(tmp_path / "cache", ttl=86400)
    app, _client = _make_app(session, disk_cache)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/cover/RJ000001")

    assert r.status_code == 200
    assert r.content == body
    assert session.calls == 2
    assert disk_cache.get_cover("RJ000001") == (body, "image/jpeg")


@pytest.mark.asyncio
async def test_cover_concurrency_is_bounded(tmp_path) -> None:
    body = make_jpeg(120, 160)
    session = _FakeCoverSession([_FakeCoverResponse(200, body)])
    disk_cache = ImageCache(tmp_path / "cache", ttl=86400)
    app, client = _make_app(session, disk_cache, concurrency=2)

    works = [_make_work(f"RJ{i:06d}") for i in range(6)]
    client.get_purchases = AsyncMock(return_value=[(w, None) for w in works])

    transport = ASGITransport(app=app)

    async def _fetch(pid: str) -> int:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(f"/cover/{pid}")
            return r.status_code

    statuses = await asyncio.gather(*[_fetch(w.product_id) for w in works])
    assert statuses == [200] * 6
    assert session.max_inflight <= 2
