"""Tests for the per-user ClientPool."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dlsite_opds.core.auth import ClientPool


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.initialize = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture()
def pool() -> ClientPool:
    return ClientPool(cache_ttl=300)


class TestClientPool:
    @pytest.mark.asyncio
    @patch("dlsite_opds.core.auth.DlsiteClient", side_effect=lambda *a, **kw: _mock_client())
    async def test_creates_client_on_first_call(
        self, mock_cls: MagicMock, pool: ClientPool
    ) -> None:
        client = await pool.get_or_create("user_a", "pass_a")
        assert client is not None
        client.initialize.assert_awaited_once()
        mock_cls.assert_called_once_with("user_a", "pass_a", 300)

    @pytest.mark.asyncio
    @patch("dlsite_opds.core.auth.DlsiteClient", side_effect=lambda *a, **kw: _mock_client())
    async def test_returns_cached_client_on_second_call(
        self, mock_cls: MagicMock, pool: ClientPool
    ) -> None:
        first = await pool.get_or_create("user_a", "pass_a")
        second = await pool.get_or_create("user_a", "pass_a")
        assert first is second
        assert mock_cls.call_count == 1

    @pytest.mark.asyncio
    @patch("dlsite_opds.core.auth.DlsiteClient", side_effect=lambda *a, **kw: _mock_client())
    async def test_different_users_get_different_clients(
        self, mock_cls: MagicMock, pool: ClientPool
    ) -> None:
        a = await pool.get_or_create("user_a", "pass_a")
        b = await pool.get_or_create("user_b", "pass_b")
        assert a is not b
        assert mock_cls.call_count == 2

    @pytest.mark.asyncio
    @patch("dlsite_opds.core.auth.DlsiteClient", side_effect=lambda *a, **kw: _mock_client())
    async def test_close_all_closes_every_client(
        self, mock_cls: MagicMock, pool: ClientPool
    ) -> None:
        a = await pool.get_or_create("user_a", "pass_a")
        b = await pool.get_or_create("user_b", "pass_b")
        await pool.close_all()
        a.close.assert_awaited_once()
        b.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("dlsite_opds.core.auth.DlsiteClient", side_effect=lambda *a, **kw: _mock_client())
    async def test_close_all_clears_pool(
        self, mock_cls: MagicMock, pool: ClientPool
    ) -> None:
        await pool.get_or_create("user_a", "pass_a")
        await pool.close_all()

        second = await pool.get_or_create("user_a", "pass_a")
        assert mock_cls.call_count == 2
        second.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_failure_propagates_and_does_not_cache(
        self, pool: ClientPool
    ) -> None:
        bad_client = _mock_client()
        bad_client.initialize = AsyncMock(side_effect=RuntimeError("login failed"))

        with patch(
            "dlsite_opds.core.auth.DlsiteClient", return_value=bad_client
        ):
            with pytest.raises(RuntimeError, match="login failed"):
                await pool.get_or_create("bad_user", "bad_pass")

        good_client = _mock_client()
        with patch(
            "dlsite_opds.core.auth.DlsiteClient", return_value=good_client
        ):
            result = await pool.get_or_create("bad_user", "bad_pass")
            assert result is good_client

    @pytest.mark.asyncio
    @patch("dlsite_opds.core.auth.DlsiteClient", side_effect=lambda *a, **kw: _mock_client())
    async def test_concurrent_calls_create_only_one_client(
        self, mock_cls: MagicMock, pool: ClientPool
    ) -> None:
        results = await asyncio.gather(
            pool.get_or_create("user_c", "pass_c"),
            pool.get_or_create("user_c", "pass_c"),
            pool.get_or_create("user_c", "pass_c"),
        )
        assert results[0] is results[1] is results[2]
        assert mock_cls.call_count == 1
