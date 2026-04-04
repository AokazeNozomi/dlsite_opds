"""Long-lived DLsite PlayAPI session with purchase and ziptree caching."""

import asyncio
import logging
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias, TypeVar

import aiohttp
from aiohttp import ClientTimeout
from dlsite_async import PlayAPI, Work
from dlsite_async.play.epub import EpubReflowableSession
from dlsite_async.play.models import DownloadToken, PlayFile, ZipTree
from dlsite_async.work import WorkType

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

PurchaseList: TypeAlias = list[tuple[Work, datetime | None]]


def _natural_sort_key(s: str) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


@dataclass
class WorkPageData:
    """Cached page-index and token for a single work."""

    page_count: int
    pages: list[tuple[str, PlayFile]]
    all_files: list[tuple[str, PlayFile]]
    ziptree: ZipTree | None = None
    token: DownloadToken | None = None
    token_fetched: float = 0.0


class DlsiteClient:
    """Wrapper around :class:`PlayAPI`.

    * Lazy login on first use.
    * In-memory purchases cache with configurable TTL.
    * Per-work download-token and ziptree caching (tokens refreshed on
      expiry).
    """

    def __init__(
        self,
        login_id: str = "",
        password: str = "",
        cache_ttl: int = 300,
    ) -> None:
        self._login_id = login_id
        self._password = password
        self._cache_ttl = cache_ttl

        self._api: PlayAPI | None = None
        self._purchases: PurchaseList = []
        self._purchases_updated: float = 0
        self._work_cache: dict[str, WorkPageData] = {}
        self._work_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._reauth_lock = asyncio.Lock()
        self._auth_generation: int = 0

    # -- lifecycle -----------------------------------------------------------

    async def _login(self) -> None:
        if self._login_id and self._password:
            await self.api.login(self._login_id, self._password)
            logger.info("Logged in to DLsite Play")
        else:
            logger.warning("No DLsite credentials configured")

    _API_TIMEOUT = ClientTimeout(total=None, connect=30, sock_read=120)

    async def initialize(self) -> None:
        self._api = PlayAPI(timeout=self._API_TIMEOUT)
        await self._api.__aenter__()
        await self._login()

    @property
    def api(self) -> PlayAPI:
        if self._api is None:
            raise RuntimeError("DlsiteClient not initialised — call initialize() first")
        return self._api

    async def close(self) -> None:
        if self._api:
            await self._api.close()
            self._api = None

    # -- re-authentication ---------------------------------------------------

    @staticmethod
    def _is_retriable(exc: Exception) -> bool:
        if isinstance(exc, aiohttp.ClientResponseError) and exc.status in (401, 403):
            return True
        # A closed connector means a concurrent _reauth destroyed the
        # session while this request was in-flight.  Retrying with the
        # freshly created session is the right thing to do.
        if isinstance(exc, aiohttp.ClientConnectionError):
            return True
        return False

    async def _reauth(self, generation: int) -> None:
        """Recreate the PlayAPI session and log in again.

        Uses *generation* to avoid redundant reauth when multiple
        concurrent requests all observe the same expired session.
        Invalidates download tokens but preserves cached page lists
        to avoid expensive ziptree re-fetches.
        """
        async with self._reauth_lock:
            if self._auth_generation != generation:
                return
            logger.info("Session expired — re-authenticating with DLsite Play…")
            if self._api:
                await self._api.close()
            self._api = PlayAPI(timeout=self._API_TIMEOUT)
            await self._api.__aenter__()
            await self._login()
            self._purchases_updated = 0
            for data in self._work_cache.values():
                data.token = None
            self._auth_generation += 1

    async def _with_reauth(self, fn: Callable[[], Awaitable[_T]]) -> _T:
        """Call *fn*; on auth/connection error, re-authenticate once and retry."""
        gen = self._auth_generation
        try:
            return await fn()
        except Exception as exc:
            if not self._is_retriable(exc):
                raise
            await self._reauth(gen)
            return await fn()

    # -- purchases -----------------------------------------------------------

    async def get_purchases(self) -> PurchaseList:
        async with self._lock:
            now = time.time()
            if self._purchases and (now - self._purchases_updated) < self._cache_ttl:
                return self._purchases

            async def _fetch() -> PurchaseList:
                return [
                    (work, purchase_date)
                    async for work, purchase_date in self.api.purchases()
                ]

            purchases = await self._with_reauth(_fetch)
            purchases.sort(key=lambda p: p[1] or datetime.min, reverse=True)
            self._purchases = purchases
            self._purchases_updated = now
            logger.info("Cached %d purchases", len(purchases))
            return self._purchases

    # -- per-work ziptree / page list ----------------------------------------

    def _work_lock(self, product_id: str) -> asyncio.Lock:
        lock = self._work_locks.get(product_id)
        if lock is None:
            lock = asyncio.Lock()
            self._work_locks[product_id] = lock
        return lock

    async def get_work_page_data(self, product_id: str) -> WorkPageData:
        cached = self._work_cache.get(product_id)
        if cached is not None and cached.token is not None:
            if cached.token.expires_at.timestamp() > time.time():
                return cached

        async with self._work_lock(product_id):
            cached = self._work_cache.get(product_id)
            if cached is not None and cached.token is not None:
                if cached.token.expires_at.timestamp() > time.time():
                    return cached

            async def _fetch() -> WorkPageData:
                token = await self.api.download_token(product_id)
                if cached is not None and (cached.pages or cached.all_files):
                    cached.token = token
                    cached.token_fetched = time.time()
                    return cached
                tree = await self.api.ziptree(token)
                pages = _extract_pages(tree, work_type=self._work_type_for(product_id))
                all_files = _extract_all_files(tree)
                return WorkPageData(
                    page_count=len(pages),
                    pages=pages,
                    all_files=all_files,
                    ziptree=tree,
                    token=token,
                    token_fetched=time.time(),
                )

            data = await self._with_reauth(_fetch)
            self._work_cache[product_id] = data
            return data

    def get_cached_page_count(self, product_id: str) -> int | None:
        cached = self._work_cache.get(product_id)
        return cached.page_count if cached else None

    def get_cached_work_page_data(self, product_id: str) -> WorkPageData | None:
        """Return cached ziptree-derived data without contacting the API."""
        return self._work_cache.get(product_id)

    def _work_type_for(self, product_id: str) -> WorkType | None:
        for work, _ in self._purchases:
            if work.product_id == product_id:
                return work.work_type
        return None

    async def ensure_valid_token(self, product_id: str) -> WorkPageData:
        data = self._work_cache.get(product_id)
        if (
            data is None
            or data.token is None
            or data.token.expires_at.timestamp() <= time.time()
        ):
            return await self.get_work_page_data(product_id)
        return data

    # -- file download -------------------------------------------------------

    async def _download_playfile(
        self, token: DownloadToken, playfile: PlayFile
    ) -> tuple[bytes, str]:
        """Download a single file via its optimized URL.

        Falls back to ``optimized/{hashname}`` when no optimized version
        metadata is available (common for PDFs and other document files).
        If the ``optimized/`` path returns 404 (e.g. for voicecomic_v2
        or other non-standard file types), tries the bare hashname path.
        """
        try:
            name = playfile.optimized_name
        except Exception:
            name = playfile.hashname
            logger.info(
                "No optimized_name for %s (type=%s, files=%r), "
                "falling back to hashname",
                playfile.hashname,
                playfile.type,
                playfile.files,
            )
        url = f"{token.url}optimized/{name}"
        async with self.api.get(url, timeout=self.api._DL_TIMEOUT) as resp:
            if resp.status == 404:
                resp.release()
            else:
                body = await resp.read()
                content_type = resp.content_type or "application/octet-stream"
                return body, content_type

        bare_url = f"{token.url}{playfile.hashname}"
        logger.info(
            "optimized/ path returned 404, trying bare URL for %s",
            playfile.hashname,
        )
        async with self.api.get(bare_url, timeout=self.api._DL_TIMEOUT) as resp:
            resp.raise_for_status()
            body = await resp.read()
            content_type = resp.content_type or "application/octet-stream"
        return body, content_type

    async def download_page_image(
        self, product_id: str, page_index: int
    ) -> tuple[bytes, PlayFile]:
        async def _fetch() -> tuple[bytes, PlayFile]:
            data = await self.ensure_valid_token(product_id)
            if page_index < 0 or page_index >= len(data.pages):
                raise IndexError(
                    f"Page {page_index} out of range (0..{len(data.pages) - 1})"
                )
            _path, playfile = data.pages[page_index]
            image_bytes, _ = await self._download_playfile(data.token, playfile)  # type: ignore[arg-type]
            return image_bytes, playfile

        return await self._with_reauth(_fetch)

    async def download_file(self, product_id: str, file_hash: str) -> tuple[bytes, str]:
        """Download a file from a work's ziptree by its hashname.

        Returns ``(body, content_type)``.

        Raises:
            KeyError: No file with *file_hash* exists in the ziptree.
        """

        async def _fetch() -> tuple[bytes, str]:
            data = await self.ensure_valid_token(product_id)
            playfile = next(
                (pf for _, pf in data.all_files if pf.hashname == file_hash), None
            )
            if playfile is None:
                raise KeyError(file_hash)
            return await self._download_playfile(data.token, playfile)  # type: ignore[arg-type]

        return await self._with_reauth(_fetch)

    async def download_epub(self, product_id: str) -> bytes:
        """Download a reflowable EPUB for a work.

        Uses :class:`EpubReflowableSession` to handle the CSR-R viewer
        token exchange, entry-by-entry download, and deobfuscation.

        Returns the assembled ``.epub`` file as bytes.

        Raises:
            ValueError: The work has no ``epub_reflowable`` playfile or
                its cached ZipTree is unavailable.
        """

        async def _fetch() -> bytes:
            data = await self.ensure_valid_token(product_id)
            playfile = _find_epub_reflowable(data)
            if playfile is None:
                raise ValueError(f"No epub_reflowable playfile found for {product_id}")
            if data.ziptree is None:
                raise ValueError(f"ZipTree not available for {product_id}")
            async with EpubReflowableSession(
                self.api, data.ziptree, playfile, product_id
            ) as session:
                with tempfile.TemporaryDirectory() as tmpdir:
                    epub_path = await session.download_epub(tmpdir)
                    return epub_path.read_bytes()

        return await self._with_reauth(_fetch)


# -- helpers -----------------------------------------------------------------


def _find_epub_reflowable(data: WorkPageData) -> PlayFile | None:
    """Return the first ``epub_reflowable`` PlayFile in *data*, if any."""
    for _path, pf in data.all_files:
        if pf.is_epub_reflowable:
            return pf
    return None


_PAGE_TYPES = frozenset({"image", "pdf"})

_IMAGE_WORK_TYPES: frozenset[WorkType] = frozenset({
    WorkType.MANGA,
    WorkType.GEKIGA,
    WorkType.WEBTOON,
    WorkType.CG_ILLUSTRATIONS,
    WorkType.ILLUST_MATERIALS,
})


def _expand_pdf_pages(path: str, playfile: PlayFile) -> list[tuple[str, PlayFile]]:
    """Expand a PDF PlayFile into individual page PlayFiles.

    DLsite represents PDF works as a single PlayFile whose ``files``
    dict contains a ``page`` list.  Each entry has an ``optimized``
    sub-dict identical to the one used by image PlayFiles.
    """
    page_list = playfile.files.get("page")
    if not isinstance(page_list, list):
        return []
    result: list[tuple[str, PlayFile]] = []
    for idx, page_data in enumerate(page_list):
        opt = page_data.get("optimized")
        if not opt or "name" not in opt:
            continue
        synthetic = PlayFile(
            length=opt.get("length", 0),
            type="image",
            files={"optimized": opt},
            hashname=opt["name"],
        )
        page_path = f"{path}#{idx:04d}"
        result.append((page_path, synthetic))
    return result


def _extract_pages(
    tree: ZipTree, work_type: WorkType | None = None
) -> list[tuple[str, PlayFile]]:
    """Return an ordered list of image pages from a ziptree.

    Handles both regular image PlayFiles and PDF PlayFiles (whose pages
    are expanded into individual synthetic entries).  When both exist,
    PDF-expanded pages are preferred and standalone images are discarded
    for image-native types (manga, CG, illustrations).  For all other
    types, standalone images are placed before the PDF pages.
    """
    image_pages: list[tuple[str, PlayFile]] = []
    pdf_pages: list[tuple[str, PlayFile]] = []
    for path, playfile in tree.items():
        if playfile.type not in _PAGE_TYPES:
            continue
        if playfile.type == "pdf":
            pdf_pages.extend(_expand_pdf_pages(path, playfile))
            continue
        try:
            _ = playfile.optimized_name
            image_pages.append((path, playfile))
        except Exception:
            continue
    image_pages.sort(key=lambda p: _natural_sort_key(p[0]))
    pdf_pages.sort(key=lambda p: _natural_sort_key(p[0]))
    if pdf_pages and image_pages and work_type not in _IMAGE_WORK_TYPES:
        return image_pages + pdf_pages
    return pdf_pages if pdf_pages else image_pages


def _extract_all_files(tree: ZipTree) -> list[tuple[str, PlayFile]]:
    """Return an ordered list of all files from a ziptree.

    Unlike :func:`_extract_pages`, this includes files regardless of
    type or whether they have an ``optimized_name``.
    """
    files: list[tuple[str, PlayFile]] = []
    for path, playfile in tree.items():
        files.append((path, playfile))
    files.sort(key=lambda p: _natural_sort_key(p[0]))
    if files:
        types = {pf.type for _, pf in files}
        logger.debug("Ziptree contains %d files, types: %s", len(files), types)
    return files
