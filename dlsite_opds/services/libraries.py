"""Content-type library definitions for per-category OPDS feeds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dlsite_async.work import WorkType

if TYPE_CHECKING:
    from ..core.play_client import PurchaseList


@dataclass(frozen=True)
class Library:
    slug: str
    title: str
    description: str
    work_types: frozenset[WorkType] = field(default_factory=frozenset)
    is_catchall: bool = False


LIBRARIES: list[Library] = [
    Library(
        slug="manga",
        title="Manga / Doujinshi",
        description="Manga, gekiga, and webtoon works",
        work_types=frozenset({WorkType.MANGA, WorkType.GEKIGA, WorkType.WEBTOON}),
    ),
    Library(
        slug="cg-illustrations",
        title="CG / Illustrations",
        description="CG sets and illustration materials",
        work_types=frozenset(
            {WorkType.CG_ILLUSTRATIONS, WorkType.ILLUST_MATERIALS}
        ),
    ),
    Library(
        slug="novels",
        title="Novels / Digital Novels",
        description="Novels, digital novels, and publications",
        work_types=frozenset(
            {WorkType.NOVEL, WorkType.DIGITAL_NOVEL, WorkType.PUBLICATION}
        ),
    ),
    Library(
        slug="other",
        title="Other",
        description="Games, audio, video, and other non-reading works",
        is_catchall=True,
    ),
]

_CATEGORIZED_WORK_TYPES: frozenset[WorkType] = frozenset().union(
    *(lib.work_types for lib in LIBRARIES if not lib.is_catchall)
)

_SLUG_INDEX: dict[str, Library] = {lib.slug: lib for lib in LIBRARIES}


def get_library(slug: str) -> Library | None:
    return _SLUG_INDEX.get(slug)


def prepare_opds_purchases(purchases: PurchaseList) -> PurchaseList:
    """Keep all purchases with a known work type."""
    return [
        (work, date)
        for work, date in purchases
        if work.work_type is not None
    ]


def filter_purchases(
    purchases: PurchaseList, library: Library
) -> PurchaseList:
    """Return only the purchases that belong to *library*."""
    if library.is_catchall:
        return [
            (work, date)
            for work, date in purchases
            if work.work_type not in _CATEGORIZED_WORK_TYPES
        ]
    return [
        (work, date)
        for work, date in purchases
        if work.work_type in library.work_types
    ]
