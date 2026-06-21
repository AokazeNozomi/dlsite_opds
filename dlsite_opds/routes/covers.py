"""Cover image proxy route."""

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..core.auth import AuthContext, get_auth
from ..core.config import Settings
from ..core.http_utils import CATALOG_IMAGE_HEADERS, jpeg_etag
from ..services.image_cache import ImageCache

logger = logging.getLogger(__name__)

router = APIRouter()


def _cover_response(
    body: bytes,
    content_type: str,
    request: Request,
) -> Response:
    etag = f'"{jpeg_etag(body)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "public, max-age=86400"},
        )
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400", "ETag": etag},
    )


async def _fetch_cover_upstream(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
    retries: int,
    retry_delay: float,
) -> tuple[bytes, str]:
    """Fetch a catalog cover with bounded concurrency and transient retries."""
    last_status: int | None = None
    for attempt in range(retries):
        try:
            async with semaphore:
                async with session.get(url, headers=CATALOG_IMAGE_HEADERS) as resp:
                    if resp.status != 200:
                        last_status = resp.status
                        if attempt + 1 < retries:
                            await asyncio.sleep(retry_delay * (attempt + 1))
                            continue
                        raise HTTPException(
                            status_code=502,
                            detail=f"Cover fetch returned {resp.status}",
                        )
                    body = await resp.read()
                    content_type = resp.content_type or "image/jpeg"
                    return body, content_type
        except aiohttp.ClientError as exc:
            logger.debug(
                "Cover fetch attempt %d failed for %s: %s",
                attempt + 1,
                url,
                exc,
            )
            if attempt + 1 < retries:
                await asyncio.sleep(retry_delay * (attempt + 1))
                continue
            logger.exception("Failed to fetch cover from %s", url)
            raise HTTPException(status_code=502, detail="Cover fetch failed") from exc

    raise HTTPException(
        status_code=502,
        detail=f"Cover fetch returned {last_status}",
    )


@router.get("/cover/{product_id}")
async def cover_image(
    request: Request,
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> Response:
    """Proxy and cache the cover/thumbnail image for a work.

    Fetching covers through the OPDS server avoids rate-limiting and
    hotlink issues when the client loads many thumbnails at once.
    """
    cover_cache: dict[str, tuple[bytes, str]] = request.app.state.cover_cache
    disk_cache: ImageCache = request.app.state.image_cache
    cfg: Settings = request.app.state.settings
    semaphore: asyncio.Semaphore = request.app.state.cover_semaphore
    session: aiohttp.ClientSession = request.app.state.cover_session

    cached = cover_cache.get(product_id)
    if cached is not None:
        body, content_type = cached
        return _cover_response(body, content_type, request)

    disk_cached = await asyncio.to_thread(disk_cache.get_cover, product_id)
    if disk_cached is not None:
        body, content_type = disk_cached
        cover_cache[product_id] = (body, content_type)
        return _cover_response(body, content_type, request)

    purchases = await auth.client.get_purchases()
    work = next((w for w, _ in purchases if w.product_id == product_id), None)
    if not work or not work.work_image:
        raise HTTPException(status_code=404, detail="No cover image available")

    url = work.work_image
    if url.startswith("//"):
        url = "https:" + url

    body, content_type = await _fetch_cover_upstream(
        session,
        url,
        semaphore,
        cfg.cover_fetch_retries,
        cfg.cover_retry_delay,
    )

    cover_cache[product_id] = (body, content_type)
    await asyncio.to_thread(disk_cache.put_cover, product_id, body, content_type)

    return _cover_response(body, content_type, request)
