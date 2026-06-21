"""Tests for crypt page download retry logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from dlsite_opds.core.play_client import DlsiteClient, WorkPageData
from dlsite_opds.services.pse import CryptImageError, is_auth_failure

from .conftest import FakePlayFile, make_jpeg


class TestIsAuthFailure:
    def test_401_and_403(self) -> None:
        assert is_auth_failure(401) is True
        assert is_auth_failure(403) is True

    def test_other_status(self) -> None:
        assert is_auth_failure(404) is False
        assert is_auth_failure(200) is False


@pytest.mark.asyncio
async def test_crypt_download_retries_after_403_then_succeeds() -> None:
    client = DlsiteClient()
    client._api = MagicMock()

    pf = FakePlayFile(crypt=True, width=800, height=600)
    good_bytes = make_jpeg(800, 600)
    token = MagicMock()
    token.url = "https://cdn.example/"

    data = WorkPageData(
        page_count=1,
        pages=[("page.jpg", pf)],
        all_files=[("page.jpg", pf)],
        token=token,
    )
    client._work_cache["RJ000001"] = data

    responses = [
        (b"bad", "image/jpeg", 403, None),
        (good_bytes, "image/jpeg", 200, 1000),
    ]

    async def fake_download(_token, _pf, *, use_image_headers=False):
        return responses.pop(0)

    refresh = AsyncMock(return_value=data)
    client._download_playfile = AsyncMock(side_effect=fake_download)  # type: ignore[method-assign]
    client.refresh_download_token = refresh  # type: ignore[method-assign]

    body, returned_pf = await client._download_crypt_page_image("RJ000001", token, pf)  # type: ignore[arg-type]

    assert body == good_bytes
    assert returned_pf is pf
    assert client._download_playfile.call_count == 2
    refresh.assert_called_once_with("RJ000001")


@pytest.mark.asyncio
async def test_crypt_download_raises_after_max_attempts() -> None:
    client = DlsiteClient()
    client._api = MagicMock()

    pf = FakePlayFile(crypt=True, width=800, height=600)
    token = MagicMock()
    bad = make_jpeg(400, 300)

    data = WorkPageData(
        page_count=1,
        pages=[("page.jpg", pf)],
        all_files=[("page.jpg", pf)],
        token=token,
    )
    client._work_cache["RJ000001"] = data

    client._download_playfile = AsyncMock(  # type: ignore[method-assign]
        return_value=(bad, "image/jpeg", 200, 1000)
    )
    client.refresh_download_token = AsyncMock(return_value=data)  # type: ignore[method-assign]

    with pytest.raises(CryptImageError):
        await client._download_crypt_page_image("RJ000001", token, pf)  # type: ignore[arg-type]

    assert client._download_playfile.call_count == 3


@pytest.mark.asyncio
async def test_download_playfile_uses_image_headers_for_crypt() -> None:
    client = DlsiteClient()
    api = MagicMock()
    client._api = api

    pf = FakePlayFile(crypt=True, width=800, height=600)
    token = MagicMock()
    token.url = "https://cdn.example/"

    resp = MagicMock()
    resp.status = 200
    resp.content_type = "image/jpeg"
    resp.headers = {"Content-Length": "1000"}
    resp.read = AsyncMock(return_value=make_jpeg(800, 600))
    resp.release = MagicMock()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    api.get = MagicMock(return_value=resp)
    api._DL_TIMEOUT = 30

    body, _ctype, status, _clen = await client._download_playfile(
        token, pf, use_image_headers=True  # type: ignore[arg-type]
    )

    assert status == 200
    assert body
    _args, kwargs = api.get.call_args
    assert kwargs.get("headers") is not None
    assert "User-Agent" in kwargs["headers"]
