"""Chapter grouping from DLsite Play ziptrees."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dlsite_async.play.models import PlayFile, ZipTree

_PAGE_TYPES = frozenset({"image", "pdf"})


def natural_sort_key(s: str) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def expand_pdf_pages(path: str, playfile: PlayFile) -> list[tuple[str, PlayFile]]:
    """Expand a PDF PlayFile into individual page PlayFiles."""
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


@dataclass
class ChapterGroup:
    """A logical chapter: image folder or expanded PDF."""

    key: str
    title: str
    pages: list[tuple[str, PlayFile]]


def parent_folder_path(path: str) -> str:
    """Return the parent folder path for a ziptree entry (``root`` for top-level)."""
    parent, _, _ = path.rpartition("/")
    return parent if parent else "root"


def extract_chapter_groups(tree: ZipTree) -> list[ChapterGroup]:
    """Extract ordered chapter groups from a ziptree.

    Image files with ``optimized_name`` are grouped by parent folder path.
    Each PDF is expanded into its own chapter.
    """
    image_groups: dict[str, list[tuple[str, PlayFile]]] = {}
    pdf_groups: dict[str, list[tuple[str, PlayFile]]] = {}

    for path, playfile in tree.items():
        if playfile.type not in _PAGE_TYPES:
            continue
        if playfile.type == "pdf":
            pages = expand_pdf_pages(path, playfile)
            if pages:
                pdf_groups.setdefault(path, []).extend(pages)
            continue
        try:
            _ = playfile.optimized_name
        except Exception:
            continue
        folder = parent_folder_path(path)
        image_groups.setdefault(folder, []).append((path, playfile))

    chapters: list[ChapterGroup] = []

    for folder, pages in image_groups.items():
        pages.sort(key=lambda p: natural_sort_key(p[0]))
        title = "root" if folder == "root" else folder
        chapters.append(
            ChapterGroup(key=f"img:{folder}", title=title, pages=pages)
        )

    for pdf_path, pages in pdf_groups.items():
        pages.sort(key=lambda p: natural_sort_key(p[0]))
        chapters.append(
            ChapterGroup(key=f"pdf:{pdf_path}", title=pdf_path, pages=pages)
        )

    chapters.sort(key=lambda ch: natural_sort_key(ch.title))
    return chapters
