"""Work download and file proxy routes."""

import asyncio
import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor

import aiohttp
from dlsite_async import Work
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..core.auth import AuthContext, get_auth
from ..core.http_utils import content_disposition, ext_for_content_type
from ..core.play_client import DlsiteClient, WorkPageData, _find_epub_reflowable
from ..services.cbz import build_comic_info
from ..services.pse import process_page_image

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/download/{product_id}")
async def download_work(
    request: Request,
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> Response:
    """Download a work in the best available format.

    Image-based works are packaged into a CBZ archive with a
    ``ComicInfo.xml``.  Document works (PDF, etc.) are served as-is.
    """
    client = auth.client
    executor: ThreadPoolExecutor = request.app.state.image_executor
    loop = asyncio.get_running_loop()

    try:
        data = await client.get_work_page_data(product_id)
    except Exception:
        logger.exception("Failed to fetch page data for %s", product_id)
        raise HTTPException(status_code=502, detail="Failed to fetch work data")

    purchases = await client.get_purchases()
    work = next((w for w, _ in purchases if w.product_id == product_id), None)

    filename_base = product_id
    if work:
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", work.work_name)
        filename_base = f"{safe_name} [{product_id}]"

    if _find_epub_reflowable(data) is not None:
        return await _serve_epub(client, product_id, filename_base)

    if data.page_count > 0:
        return await _serve_cbz(client, data, work, product_id, filename_base,
                                executor, loop)

    if data.all_files:
        logger.info(
            "Serving raw file for %s (%d files, types: %s)",
            product_id,
            len(data.all_files),
            {pf.type for _, pf in data.all_files},
        )
        return await _serve_raw_file(client, data, product_id, filename_base)

    logger.warning("No downloadable files found for %s", product_id)
    raise HTTPException(status_code=404, detail="No downloadable files found")


async def _serve_cbz(
    client: DlsiteClient,
    data: WorkPageData,
    work: Work | None,
    product_id: str,
    filename_base: str,
    executor: ThreadPoolExecutor,
    loop: asyncio.AbstractEventLoop,
) -> Response:
    """Build and return a CBZ archive from the work's image pages."""
    sem = asyncio.Semaphore(4)

    async def _download_page(idx: int) -> tuple[int, bytes]:
        async with sem:
            image_bytes, playfile = await client.download_page_image(
                product_id, idx
            )
            jpeg = await loop.run_in_executor(
                executor, process_page_image, image_bytes, playfile, None
            )
            return idx, jpeg

    results = await asyncio.gather(
        *[_download_page(i) for i in range(data.page_count)]
    )
    results_sorted: list[tuple[int, bytes]] = sorted(results, key=lambda r: r[0])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("ComicInfo.xml", build_comic_info(work, data.page_count))
        for idx, jpeg_bytes in results_sorted:
            zf.writestr(f"{idx:04d}.jpg", jpeg_bytes)

    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.comicbook+zip",
        headers={
            "Content-Disposition": content_disposition(f"{filename_base}.cbz"),
        },
    )


async def _serve_raw_file(
    client: DlsiteClient,
    data: WorkPageData,
    product_id: str,
    filename_base: str,
) -> Response:
    """Download and serve the primary non-image file from a work."""
    _path, playfile = data.all_files[0]
    try:
        body, content_type = await client.download_file(product_id, playfile.hashname)
    except (aiohttp.ClientResponseError, KeyError) as exc:
        logger.warning(
            "Failed to download raw file for %s (type=%s): %s",
            product_id, playfile.type, exc,
        )
        raise HTTPException(
            status_code=404, detail="File not downloadable"
        ) from exc
    ext = ext_for_content_type(content_type)

    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Content-Disposition": content_disposition(f"{filename_base}{ext}"),
        },
    )


async def _serve_epub(
    client: DlsiteClient,
    product_id: str,
    filename_base: str,
) -> Response:
    """Download and serve a reflowable EPUB from DLsite Play."""
    epub_bytes = await client.download_epub(product_id)
    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": content_disposition(f"{filename_base}.epub"),
        },
    )


@router.get("/files/{product_id}/{file_hash}")
async def file_proxy(
    product_id: str,
    file_hash: str,
    auth: AuthContext = Depends(get_auth),
) -> Response:
    """Stream an individual file from a work's ziptree.

    ``file_hash`` is the PlayFile hashname -- only files present in the
    work's ziptree are served (path-traversal safe by design).
    """
    try:
        body, content_type = await auth.client.download_file(product_id, file_hash)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found in ziptree")
    except Exception:
        logger.exception("Upstream download failed for %s/%s", product_id, file_hash)
        raise HTTPException(status_code=502, detail="Upstream download failed")

    return Response(content=body, media_type=content_type)
