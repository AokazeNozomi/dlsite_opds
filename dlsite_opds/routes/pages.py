"""OPDS-PSE page streaming routes and background prefetcher."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from ..core.auth import AuthContext, SourceImageLRU, get_auth
from ..core.config import Settings
from ..core.http_utils import jpeg_response, spawn_background
from ..services.image_cache import ImageCache
from ..core.play_client import DlsiteClient
from ..services.pse import prepare_source_image, resize_and_encode

logger = logging.getLogger(__name__)

router = APIRouter()


async def prefetch_pages(
    app_state: object,
    client: DlsiteClient,
    product_id: str,
    start_page: int,
    max_width: int | None,
    count: int,
) -> None:
    """Download, process, and cache upcoming pages in the background."""
    cache: ImageCache = app_state.image_cache  # type: ignore[union-attr]
    source_cache: SourceImageLRU = app_state.source_cache  # type: ignore[union-attr]
    executor: ThreadPoolExecutor = app_state.image_executor  # type: ignore[union-attr]
    inflight: set[tuple[str, int, int | None]] = app_state.prefetch_inflight  # type: ignore[union-attr]
    loop = asyncio.get_running_loop()

    page_count = client.get_cached_page_count(product_id)
    end_page = min(start_page + count, page_count) if page_count else start_page + count

    async def _do_one(pg: int) -> None:
        key = (product_id, pg, max_width)
        if key in inflight:
            return
        inflight.add(key)
        try:
            if await asyncio.to_thread(cache.get, product_id, pg, max_width) is not None:
                return
            image_bytes, playfile = await client.download_page_image(product_id, pg)
            source_im = await loop.run_in_executor(
                executor, prepare_source_image, image_bytes, playfile
            )
            source_cache.put((product_id, pg), source_im)
            jpeg = await loop.run_in_executor(
                executor, resize_and_encode, source_im, max_width
            )
            await asyncio.to_thread(cache.put, product_id, pg, max_width, jpeg)
        except Exception:
            logger.debug("Prefetch page %d of %s failed", pg, product_id)
        finally:
            inflight.discard(key)

    await asyncio.gather(*[_do_one(pg) for pg in range(start_page, end_page)])


def _maybe_prefetch(
    request: Request,
    client: DlsiteClient,
    product_id: str,
    next_page: int,
    max_width: int | None,
) -> None:
    """Spawn a background prefetch if configured."""
    cfg: Settings = request.app.state.settings
    if cfg.prefetch_ahead > 0:
        spawn_background(
            prefetch_pages(
                request.app.state, client,
                product_id, next_page, max_width, cfg.prefetch_ahead,
            )
        )


@router.get("/pse/{product_id}")
async def pse_page(
    request: Request,
    product_id: str,
    auth: AuthContext = Depends(get_auth),
    page: int = Query(0, ge=0),
    width: int | None = Query(None, ge=0),
) -> Response:
    """Serve a single page image (OPDS-PSE stream endpoint).

    * ``page`` is **0-based** per OPDS-PSE 1.0.
    * ``width`` is the optional ``{maxWidth}`` resize hint.
    """
    max_width = width if width else None
    cache: ImageCache = request.app.state.image_cache
    source_cache: SourceImageLRU = request.app.state.source_cache
    executor: ThreadPoolExecutor = request.app.state.image_executor
    loop = asyncio.get_running_loop()

    cached = await asyncio.to_thread(cache.get, product_id, page, max_width)
    if cached is not None:
        _maybe_prefetch(request, auth.client, product_id, page + 1, max_width)
        return jpeg_response(cached, request)

    source_key = (product_id, page)
    source_im = source_cache.get(source_key)

    if source_im is None:
        try:
            image_bytes, playfile = await auth.client.download_page_image(
                product_id, page
            )
        except IndexError:
            raise HTTPException(status_code=404, detail="Page not found")
        except Exception:
            logger.exception("Failed to download page %d of %s", page, product_id)
            raise HTTPException(status_code=502, detail="Upstream download failed")

        source_im = await loop.run_in_executor(
            executor, prepare_source_image, image_bytes, playfile
        )
        source_cache.put(source_key, source_im)

    jpeg = await loop.run_in_executor(
        executor, resize_and_encode, source_im, max_width
    )

    await asyncio.to_thread(cache.put, product_id, page, max_width, jpeg)

    _maybe_prefetch(request, auth.client, product_id, page + 1, max_width)

    return jpeg_response(jpeg, request)
