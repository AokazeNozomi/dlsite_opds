"""Cover image proxy route."""

import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..core.auth import AuthContext, get_auth
from ..core.http_utils import jpeg_etag

logger = logging.getLogger(__name__)

router = APIRouter()


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

    cached = cover_cache.get(product_id)
    if cached is not None:
        body, content_type = cached
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

    purchases = await auth.client.get_purchases()
    work = next((w for w, _ in purchases if w.product_id == product_id), None)
    if not work or not work.work_image:
        raise HTTPException(status_code=404, detail="No cover image available")

    url = work.work_image
    if url.startswith("//"):
        url = "https:" + url

    session: aiohttp.ClientSession = request.app.state.cover_session
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Cover fetch returned {resp.status}",
                )
            body = await resp.read()
            content_type = resp.content_type or "image/jpeg"
    except aiohttp.ClientError:
        logger.exception("Failed to fetch cover for %s", product_id)
        raise HTTPException(status_code=502, detail="Cover fetch failed")

    cover_cache[product_id] = (body, content_type)

    etag = f'"{jpeg_etag(body)}"'
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400", "ETag": etag},
    )
