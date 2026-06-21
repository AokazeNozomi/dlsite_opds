"""Reusable HTTP helpers: ETag, Content-Disposition, MIME mapping, background tasks."""

import asyncio
import hashlib
from collections.abc import Coroutine
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response

_MIME_TO_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "application/zip": ".zip",
}

# Browser-like headers for DLsite Play CDN image fetches.
PLAY_IMAGE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
    "Mobile/15E148 Safari/604.1"
)
PLAY_IMAGE_HEADERS = {
    "User-Agent": PLAY_IMAGE_USER_AGENT,
    "Referer": "https://play.dlsite.com/",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

background_tasks: set[asyncio.Task[None]] = set()


def spawn_background(coro: Coroutine[object, object, None]) -> None:
    """Create a background task with a reference so it isn't garbage-collected."""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def ext_for_content_type(content_type: str) -> str:
    """Map a MIME content type to a file extension for Content-Disposition."""
    base = content_type.split(";", 1)[0].strip().lower()
    return _MIME_TO_EXT.get(base, "")


def content_disposition(filename: str) -> str:
    """Build a ``Content-Disposition`` header value safe for non-ASCII names.

    Uses RFC 5987 ``filename*`` with UTF-8 encoding as the primary value,
    and an ASCII-only ``filename`` fallback.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    utf8_name = quote(filename, safe="")
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{utf8_name}"
    )


def jpeg_etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def jpeg_response(jpeg: bytes, request: Request) -> Response:
    """Build a JPEG response with caching headers and 304 support."""
    etag = f'"{jpeg_etag(jpeg)}"'
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, max-age=86400"},
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400", "ETag": etag},
    )
