"""On-demand work metadata resolution for OPDS feed entries."""

import asyncio
import logging
import mimetypes
from dataclasses import dataclass

from ..core.play_client import (
    DlsiteClient,
    PurchaseList,
    WorkPageData,
    _find_epub_reflowable,
)

logger = logging.getLogger(__name__)

_PATH_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".epub": "application/epub+zip",
    ".zip": "application/zip",
}


def mime_from_path(path: str) -> str:
    """Infer MIME type from a file path extension."""
    dot = path.rfind(".")
    if dot != -1:
        ext = path[dot:].lower()
        mime = _PATH_EXT_TO_MIME.get(ext)
        if mime:
            return mime
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def is_image_path(path: str) -> bool:
    mime = mimetypes.guess_type(path)[0]
    return mime is not None and mime.startswith("image/")


@dataclass
class ResolvedWorkInfo:
    """Page counts and file-link MIME types for a page of works."""

    page_counts: dict[str, int]
    file_links: dict[str, str]
    chapter_counts: dict[str, int]

    def has_content(self, pid: str) -> bool:
        return pid in self.page_counts or pid in self.file_links


async def resolve_work_metadata(
    client: DlsiteClient,
    page_slice: PurchaseList,
) -> ResolvedWorkInfo:
    """Return page counts and file-link info for every work in *page_slice*.

    Uses cached values when available and fetches the rest concurrently
    (with bounded concurrency) so that PSE streaming links are always
    present in the feed.

    For works with no image pages but with downloadable files, the
    MIME type of the primary file is recorded in ``file_links``.
    """
    page_counts: dict[str, int] = {}
    file_links: dict[str, str] = {}
    chapter_counts: dict[str, int] = {}
    missing: list[str] = []

    def _classify(pid: str, data: WorkPageData) -> None:
        if len(data.chapters) > 1:
            chapter_counts[pid] = len(data.chapters)
        if _find_epub_reflowable(data) is not None:
            file_links[pid] = "application/epub+zip"
        elif data.page_count > 0:
            page_counts[pid] = data.page_count
        elif data.all_files:
            for path, pf in data.all_files:
                if not is_image_path(path):
                    continue
                try:
                    pf.optimized_name
                except Exception:
                    continue
                file_links[pid] = mime_from_path(path)
                return

    for work, _ in page_slice:
        pid = work.product_id
        cached = client.get_cached_work_page_data(pid)
        if cached is not None:
            _classify(pid, cached)
        else:
            missing.append(pid)

    if not missing:
        return ResolvedWorkInfo(
            page_counts=page_counts,
            file_links=file_links,
            chapter_counts=chapter_counts,
        )

    sem = asyncio.Semaphore(4)

    async def _fetch_one(pid: str) -> tuple[str, WorkPageData | None]:
        async with sem:
            try:
                return pid, await client.get_work_page_data(pid)
            except Exception:
                logger.debug("Page-data fetch failed for %s", pid, exc_info=True)
                return pid, None

    logger.debug(
        "Resolving metadata: %d works (%d cached, %d to fetch)",
        len(page_slice),
        len(page_slice) - len(missing),
        len(missing),
    )
    fetched = await asyncio.gather(*[_fetch_one(pid) for pid in missing])
    for pid, data in fetched:
        if data is None:
            continue
        _classify(pid, data)

    logger.debug(
        "Resolved metadata: %d streamable (page_counts), %d file-only, "
        "%d multi-chapter, %d with no content",
        len(page_counts),
        len(file_links),
        len(chapter_counts),
        sum(
            1
            for w, _ in page_slice
            if w.product_id not in page_counts and w.product_id not in file_links
        ),
    )
    return ResolvedWorkInfo(
        page_counts=page_counts,
        file_links=file_links,
        chapter_counts=chapter_counts,
    )
