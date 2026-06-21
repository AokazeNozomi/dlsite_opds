"""Authentication, client pooling, and per-user context."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from PIL import Image

from .play_client import DlsiteClient
from .progress import ProgressManager, ProgressStore

security = HTTPBasic()


class ClientPool:
    """Lazily creates and caches a :class:`DlsiteClient` per login ID."""

    def __init__(self, cache_ttl: int) -> None:
        self._cache_ttl = cache_ttl
        self._clients: dict[str, DlsiteClient] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, login_id: str, password: str) -> DlsiteClient:
        async with self._lock:
            existing = self._clients.get(login_id)
            if existing is not None:
                return existing
            client = DlsiteClient(login_id, password, self._cache_ttl)
            await client.initialize()
            self._clients[login_id] = client
            return client

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()


class SourceImageLRU:
    """Bounded LRU cache for descrambled PIL Images.

    Avoids re-downloading and re-descrambling the same page when it is
    requested at multiple widths within the same app lifetime.
    """

    def __init__(self, capacity: int = 32) -> None:
        self._data: OrderedDict[tuple[str, str, int], Image.Image] = OrderedDict()
        self._capacity = capacity

    @staticmethod
    def _key(
        product_id: str, page: int, chapter: str | None = None
    ) -> tuple[str, str, int]:
        return (product_id, chapter or "", page)

    def get(
        self, product_id: str, page: int, chapter: str | None = None
    ) -> Image.Image | None:
        key = self._key(product_id, page, chapter)
        try:
            self._data.move_to_end(key)
            return self._data[key]
        except KeyError:
            return None

    def put(
        self,
        product_id: str,
        page: int,
        value: Image.Image,
        chapter: str | None = None,
    ) -> None:
        key = self._key(product_id, page, chapter)
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._capacity:
            self._data.popitem(last=False)


@dataclass
class AuthContext:
    client: DlsiteClient
    progress: ProgressStore


async def get_auth(
    request: Request,
    creds: HTTPBasicCredentials = Depends(security),
) -> AuthContext:
    """Resolve the per-user DlsiteClient and ProgressStore from Basic Auth."""
    pool: ClientPool = request.app.state.pool
    pm: ProgressManager = request.app.state.progress_manager
    try:
        client = await pool.get_or_create(creds.username, creds.password)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="DLsite login failed",
            headers={"WWW-Authenticate": "Basic"},
        )
    return AuthContext(client=client, progress=pm.for_user(creds.username))
