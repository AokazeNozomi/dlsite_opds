"""OPDS-PSE page streaming routes and background prefetcher."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..core.auth import AuthContext, SourceImageLRU, get_auth
from ..core.config import Settings
from ..core.http_utils import jpeg_response, spawn_background
from ..core.play_client import DlsiteClient
from ..services.image_cache import ImageCache
from ..services.pse import prepare_source_image, resize_and_encode

logger = logging.getLogger(__name__)

router = APIRouter()


async def _prepare_page_source(
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
    image_bytes: bytes,
    playfile: object,
) -> object:
    """Decode/descramble a page; map processing errors to HTTP 502."""
    try:
        return await loop.run_in_executor(
            executor, prepare_source_image, image_bytes, playfile
        )
    except Exception as exc:
        logger.exception("Failed to prepare page source image")
        raise HTTPException(
            status_code=502, detail="Image processing failed"
        ) from exc


async def _encode_page_jpeg(
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
    source_im: object,
    max_width: int | None,
) -> bytes:
    try:
        return await loop.run_in_executor(
            executor, resize_and_encode, source_im, max_width
        )
    except Exception as exc:
        logger.exception("Failed to encode page JPEG")
        raise HTTPException(
            status_code=502, detail="Image processing failed"
        ) from exc


async def prefetch_pages(
    app_state: object,
    client: DlsiteClient,
    product_id: str,
    start_page: int,
    max_width: int | None,
    count: int,
    chapter: str | None = None,
) -> None:
    """Download, process, and cache upcoming pages in the background."""
    cache: ImageCache = app_state.image_cache  # type: ignore[union-attr]
    source_cache: SourceImageLRU = app_state.source_cache  # type: ignore[union-attr]
    executor: ThreadPoolExecutor = app_state.image_executor  # type: ignore[union-attr]
    inflight: set[tuple[str, str, int, int | None]] = app_state.prefetch_inflight  # type: ignore[union-attr]
    loop = asyncio.get_running_loop()

    page_count = client.get_cached_page_count(product_id, chapter)
    end_page = min(start_page + count, page_count) if page_count else start_page + count
    chapter_key = chapter or ""

    async def _do_one(pg: int) -> None:
        key = (product_id, chapter_key, pg, max_width)
        if key in inflight:
            return
        inflight.add(key)
        try:
            if (
                await asyncio.to_thread(
                    cache.get, product_id, pg, max_width, chapter
                )
                is not None
            ):
                return
            image_bytes, playfile = await client.download_page_image(
                product_id, pg, chapter_key=chapter
            )
            source_im = await loop.run_in_executor(
                executor, prepare_source_image, image_bytes, playfile
            )
            source_cache.put(product_id, pg, source_im, chapter=chapter)
            jpeg = await loop.run_in_executor(
                executor, resize_and_encode, source_im, max_width
            )
            await asyncio.to_thread(
                cache.put, product_id, pg, max_width, jpeg, chapter
            )
            logger.debug(
                "Prefetched page %d of %s (chapter=%s)", pg, product_id, chapter
            )
        except Exception:
            logger.debug(
                "Prefetch page %d of %s (chapter=%s) failed",
                pg,
                product_id,
                chapter,
                exc_info=True,
            )
        finally:
            inflight.discard(key)

    await asyncio.gather(*[_do_one(pg) for pg in range(start_page, end_page)])


def _maybe_prefetch(
    request: Request,
    client: DlsiteClient,
    product_id: str,
    next_page: int,
    max_width: int | None,
    chapter: str | None = None,
) -> None:
    """Spawn a background prefetch if configured."""
    cfg: Settings = request.app.state.settings
    if cfg.prefetch_ahead > 0:
        spawn_background(
            prefetch_pages(
                request.app.state, client,
                product_id, next_page, max_width, cfg.prefetch_ahead,
                chapter=chapter,
            )
        )


@router.get("/pse/{product_id}")
async def pse_page(
    request: Request,
    product_id: str,
    auth: AuthContext = Depends(get_auth),
    page: int = Query(0, ge=0),
    width: int | None = Query(None, ge=0),
    chapter: str | None = Query(None),
) -> Response:
    """Serve a single page image (OPDS-PSE stream endpoint).

    * ``page`` is **0-based** per OPDS-PSE 1.0.
    * ``width`` is the optional ``{maxWidth}`` resize hint.
    * ``chapter`` scopes pages for multi-chapter works.
    """
    max_width = width if width else None
    cache: ImageCache = request.app.state.image_cache
    source_cache: SourceImageLRU = request.app.state.source_cache
    executor: ThreadPoolExecutor = request.app.state.image_executor
    loop = asyncio.get_running_loop()
    started = time.perf_counter()

    logger.debug(
        "PSE request: product=%s page=%d width=%s chapter=%s",
        product_id,
        page,
        max_width,
        chapter,
    )

    try:
        data = await auth.client.ensure_valid_token(product_id)
    except Exception:
        logger.exception("Failed to resolve work data for %s", product_id)
        raise HTTPException(status_code=502, detail="Upstream download failed")

    logger.debug(
        "PSE work resolved: product=%s chapters=%d total_pages=%d",
        product_id,
        len(data.chapters),
        data.page_count,
    )

    try:
        chapter_pages = data.pages_for_chapter(chapter)
    except KeyError:
        logger.warning(
            "PSE chapter not found: product=%s chapter=%s available=%s",
            product_id,
            chapter,
            [ch.key for ch in data.chapters],
        )
        raise HTTPException(status_code=404, detail="Chapter not found")
    if page < 0 or page >= len(chapter_pages):
        logger.warning(
            "PSE page out of range: product=%s page=%d chapter=%s page_count=%d",
            product_id,
            page,
            chapter,
            len(chapter_pages),
        )
        raise HTTPException(status_code=404, detail="Page not found")

    cached = await asyncio.to_thread(
        cache.get, product_id, page, max_width, chapter
    )
    if cached is not None:
        logger.debug(
            "PSE disk-cache HIT: product=%s page=%d width=%s chapter=%s bytes=%d",
            product_id,
            page,
            max_width,
            chapter,
            len(cached),
        )
        _maybe_prefetch(
            request, auth.client, product_id, page + 1, max_width, chapter
        )
        return jpeg_response(cached, request)

    source_im = source_cache.get(product_id, page, chapter=chapter)

    if source_im is None:
        logger.debug(
            "PSE cache MISS: product=%s page=%d chapter=%s — downloading",
            product_id,
            page,
            chapter,
        )
        dl_started = time.perf_counter()
        try:
            image_bytes, playfile = await auth.client.download_page_image(
                product_id, page, chapter_key=chapter
            )
        except IndexError:
            raise HTTPException(status_code=404, detail="Page not found")
        except KeyError:
            raise HTTPException(status_code=404, detail="Chapter not found")
        except Exception:
            logger.exception(
                "Failed to download page %d of %s (chapter=%s)",
                page,
                product_id,
                chapter,
            )
            raise HTTPException(status_code=502, detail="Upstream download failed")

        logger.debug(
            "PSE downloaded: product=%s page=%d bytes=%d in %.0fms",
            product_id,
            page,
            len(image_bytes),
            (time.perf_counter() - dl_started) * 1000,
        )

        prep_started = time.perf_counter()
        source_im = await _prepare_page_source(
            loop, executor, image_bytes, playfile
        )
        logger.debug(
            "PSE decoded: product=%s page=%d size=%sx%s mode=%s in %.0fms",
            product_id,
            page,
            getattr(source_im, "width", "?"),
            getattr(source_im, "height", "?"),
            getattr(source_im, "mode", "?"),
            (time.perf_counter() - prep_started) * 1000,
        )
        source_cache.put(product_id, page, source_im, chapter=chapter)
    else:
        logger.debug(
            "PSE source-cache HIT: product=%s page=%d chapter=%s",
            product_id,
            page,
            chapter,
        )

    enc_started = time.perf_counter()
    jpeg = await _encode_page_jpeg(loop, executor, source_im, max_width)
    logger.debug(
        "PSE encoded: product=%s page=%d width=%s jpeg_bytes=%d in %.0fms",
        product_id,
        page,
        max_width,
        len(jpeg),
        (time.perf_counter() - enc_started) * 1000,
    )

    await asyncio.to_thread(
        cache.put, product_id, page, max_width, jpeg, chapter
    )

    _maybe_prefetch(
        request, auth.client, product_id, page + 1, max_width, chapter
    )

    logger.debug(
        "PSE response: product=%s page=%d jpeg_bytes=%d total=%.0fms",
        product_id,
        page,
        len(jpeg),
        (time.perf_counter() - started) * 1000,
    )
    return jpeg_response(jpeg, request)
