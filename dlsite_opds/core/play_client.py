"""Long-lived DLsite PlayAPI session with purchase and ziptree caching."""

import asyncio
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeAlias, TypeVar

import aiohttp
from aiohttp import ClientTimeout
from dlsite_async import PlayAPI, Work
from dlsite_async.play.epub import EpubReflowableSession
from dlsite_async.play.models import DownloadToken, PlayFile, ZipTree
from dlsite_async.work import WorkType

from ..services.chapters import ChapterGroup, expand_pdf_pages, extract_chapter_groups, natural_sort_key
from ..services.pse import (
    CryptImageError,
    MAX_CRYPT_ATTEMPTS,
    is_auth_failure,
    prepare_source_image_with_validation,
    should_retry,
)
from .http_utils import PLAY_IMAGE_HEADERS

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

PurchaseList: TypeAlias = list[tuple[Work, datetime | None]]


def _natural_sort_key(s: str) -> list[int | str]:
    return natural_sort_key(s)


@dataclass
class WorkPageData:
    """Cached page-index and token for a single work."""

    page_count: int
    pages: list[tuple[str, PlayFile]]
    all_files: list[tuple[str, PlayFile]]
    ziptree: ZipTree | None = None
    token: DownloadToken | None = None
    token_fetched: float = 0.0
    chapters: list[ChapterGroup] = field(default_factory=list)

    def pages_for_chapter(self, chapter_key: str | None) -> list[tuple[str, PlayFile]]:
        """Return pages for *chapter_key*, or the flat list when unset."""
        if chapter_key is None:
            return self.pages
        for chapter in self.chapters:
            if chapter.key == chapter_key:
                return chapter.pages
        raise KeyError(chapter_key)


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
                chapters = extract_chapter_groups(tree)
                if chapters:
                    pages: list[tuple[str, PlayFile]] = []
                    for chapter in chapters:
                        pages.extend(chapter.pages)
                else:
                    chapters = []
                    pages = _extract_pages(
                        tree, work_type=self._work_type_for(product_id)
                    )
                all_files = _extract_all_files(tree)
                logger.debug(
                    "Resolved work %s: pages=%d chapters=%d (%s) all_files=%d",
                    product_id,
                    len(pages),
                    len(chapters),
                    [f"{ch.key}:{len(ch.pages)}" for ch in chapters],
                    len(all_files),
                )
                if not pages:
                    logger.warning(
                        "Work %s resolved with 0 pages — not streamable "
                        "(all_files=%d, file_types=%s)",
                        product_id,
                        len(all_files),
                        sorted({pf.type for _, pf in all_files}),
                    )
                return WorkPageData(
                    page_count=len(pages),
                    pages=pages,
                    all_files=all_files,
                    ziptree=tree,
                    token=token,
                    token_fetched=time.time(),
                    chapters=chapters,
                )

            data = await self._with_reauth(_fetch)
            self._work_cache[product_id] = data
            return data

    def get_cached_page_count(
        self, product_id: str, chapter_key: str | None = None
    ) -> int | None:
        cached = self._work_cache.get(product_id)
        if cached is None:
            return None
        if chapter_key is not None:
            try:
                return len(cached.pages_for_chapter(chapter_key))
            except KeyError:
                return None
        return cached.page_count

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

    async def refresh_download_token(self, product_id: str) -> WorkPageData:
        """Force-fetch a new download token for *product_id*."""
        async with self._work_lock(product_id):
            token = await self.api.download_token(product_id)
            cached = self._work_cache.get(product_id)
            if cached is not None:
                cached.token = token
                cached.token_fetched = time.time()
                return cached
        return await self.get_work_page_data(product_id)

    # -- file download -------------------------------------------------------

    @staticmethod
    def _is_image_playfile(playfile: PlayFile) -> bool:
        return playfile.type in ("image", "pdf") or bool(
            playfile.files.get("optimized")
        )

    async def _download_playfile(
        self,
        token: DownloadToken,
        playfile: PlayFile,
        *,
        use_image_headers: bool = False,
    ) -> tuple[bytes, str, int, int | None]:
        """Download a single file via its optimized URL.

        Returns ``(body, content_type, http_status, content_length)``.

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
        headers = PLAY_IMAGE_HEADERS if use_image_headers else None
        logger.debug(
            "GET optimized file: name=%s image_headers=%s", name, use_image_headers
        )
        async with self.api.get(
            url, timeout=self.api._DL_TIMEOUT, headers=headers
        ) as resp:
            content_length = _parse_content_length(resp.headers.get("Content-Length"))
            if resp.status == 404:
                resp.release()
            else:
                body = await resp.read()
                content_type = resp.content_type or "application/octet-stream"
                logger.debug(
                    "GET optimized file done: name=%s status=%d content_type=%s "
                    "content_length=%s bytes=%d",
                    name,
                    resp.status,
                    content_type,
                    content_length,
                    len(body),
                )
                return body, content_type, resp.status, content_length

        bare_url = f"{token.url}{playfile.hashname}"
        logger.info(
            "optimized/ path returned 404, trying bare URL for %s",
            playfile.hashname,
        )
        async with self.api.get(
            bare_url, timeout=self.api._DL_TIMEOUT, headers=headers
        ) as resp:
            resp.raise_for_status()
            body = await resp.read()
            content_type = resp.content_type or "application/octet-stream"
            content_length = _parse_content_length(resp.headers.get("Content-Length"))
        return body, content_type, resp.status, content_length

    async def _download_crypt_page_image(
        self,
        product_id: str,
        token: DownloadToken,
        playfile: PlayFile,
    ) -> tuple[bytes, PlayFile]:
        """Download a crypt page with validation and up to 3 retry attempts."""
        last_err: Exception = CryptImageError("Crypt image download failed")
        use_image_headers = True

        for attempt in range(MAX_CRYPT_ATTEMPTS):
            body, _ctype, status, content_length = await self._download_playfile(
                token,
                playfile,
                use_image_headers=use_image_headers,
            )
            try:
                prepare_source_image_with_validation(
                    body,
                    playfile,
                    http_status=status,
                    content_length=content_length,
                )
                logger.debug(
                    "Crypt image OK on attempt %d for %s (product=%s) HTTP %d bytes=%d",
                    attempt + 1,
                    playfile.optimized_name,
                    product_id,
                    status,
                    len(body),
                )
                return body, playfile
            except CryptImageError as exc:
                logger.warning(
                    "Crypt image attempt %d/%d failed for %s (product=%s) "
                    "HTTP %d bytes=%d: %s",
                    attempt + 1,
                    MAX_CRYPT_ATTEMPTS,
                    playfile.optimized_name,
                    product_id,
                    status,
                    len(body),
                    exc,
                )
                last_err = exc
                if not should_retry(attempt, MAX_CRYPT_ATTEMPTS):
                    break
                if is_auth_failure(status):
                    logger.debug(
                        "Crypt retry: refreshing token after auth failure "
                        "(product=%s, HTTP %d)",
                        product_id,
                        status,
                    )
                    data = await self.refresh_download_token(product_id)
                    token = data.token  # type: ignore[assignment]
                elif attempt == 0:
                    logger.debug(
                        "Crypt retry: re-downloading without token refresh "
                        "(product=%s)",
                        product_id,
                    )
                    continue
                else:
                    logger.debug(
                        "Crypt retry: refreshing token (product=%s)", product_id
                    )
                    data = await self.refresh_download_token(product_id)
                    token = data.token  # type: ignore[assignment]

        logger.error(
            "Crypt image failed after %d attempts for %s (product=%s): %s",
            MAX_CRYPT_ATTEMPTS,
            playfile.optimized_name,
            product_id,
            last_err,
        )
        raise last_err

    async def download_page_image(
        self,
        product_id: str,
        page_index: int,
        chapter_key: str | None = None,
    ) -> tuple[bytes, PlayFile]:
        async def _fetch() -> tuple[bytes, PlayFile]:
            data = await self.ensure_valid_token(product_id)
            pages = data.pages_for_chapter(chapter_key)
            if page_index < 0 or page_index >= len(pages):
                raise IndexError(
                    f"Page {page_index} out of range (0..{len(pages) - 1})"
                )
            _path, playfile = pages[page_index]
            is_crypt = bool(playfile.files.get("optimized", {}).get("crypt", False))
            use_image_headers = self._is_image_playfile(playfile)
            logger.debug(
                "download_page_image: product=%s page=%d path=%s type=%s "
                "crypt=%s image_headers=%s",
                product_id,
                page_index,
                _path,
                playfile.type,
                is_crypt,
                use_image_headers,
            )

            if is_crypt:
                return await self._download_crypt_page_image(
                    product_id, data.token, playfile  # type: ignore[arg-type]
                )

            body, ctype, status, clen = await self._download_playfile(
                data.token,  # type: ignore[arg-type]
                playfile,
                use_image_headers=use_image_headers,
            )
            logger.debug(
                "download_page_image done: product=%s page=%d status=%d "
                "content_type=%s bytes=%d",
                product_id,
                page_index,
                status,
                ctype,
                len(body),
            )
            return body, playfile

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
            body, content_type, _status, _clen = await self._download_playfile(
                data.token,  # type: ignore[arg-type]
                playfile,
                use_image_headers=self._is_image_playfile(playfile),
            )
            return body, content_type

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


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


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
    return expand_pdf_pages(path, playfile)


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
