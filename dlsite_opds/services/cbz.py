"""ComicInfo.xml generation for CBZ archives."""

from __future__ import annotations

from xml.sax.saxutils import escape

from dlsite_async import Work
from dlsite_async.work import WorkType

_MANGA_WORK_TYPES: frozenset[WorkType] = frozenset({
    WorkType.MANGA,
    WorkType.GEKIGA,
    WorkType.WEBTOON,
})


def _tag(name: str, value: str | int | None) -> str:
    if value is None or value == "":
        return ""
    return f"  <{name}>{escape(str(value))}</{name}>\n"


def build_comic_info(work: Work | None, page_count: int) -> str:
    """Build a ComicInfo.xml string from DLsite work metadata.

    Returns a minimal but valid document even when *work* is ``None``
    (only ``PageCount`` will be populated).
    """
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<ComicInfo>\n'

    if work is not None:
        xml += _tag("Title", work.work_name)
        xml += _tag("Series", work.series)

        writers: list[str] = []
        if work.author:
            writers.extend(work.author)
        if work.scenario:
            writers.extend(work.scenario)
        if writers:
            xml += _tag("Writer", ", ".join(dict.fromkeys(writers)))

        artists: list[str] = []
        if work.illustration:
            artists.extend(work.illustration)
        if artists:
            xml += _tag("Penciller", ", ".join(dict.fromkeys(artists)))

        if work.circle:
            xml += _tag("Publisher", work.circle)
        elif work.brand:
            xml += _tag("Publisher", work.brand)

        if work.genre:
            xml += _tag("Genre", ", ".join(work.genre))

        if work.description:
            xml += _tag("Summary", work.description)

        if work.regist_date:
            xml += _tag("Year", work.regist_date.year)
            xml += _tag("Month", work.regist_date.month)
            xml += _tag("Day", work.regist_date.day)

        if work.language:
            xml += _tag("LanguageISO", work.language[0])

        if work.work_type in _MANGA_WORK_TYPES:
            xml += _tag("Manga", "Yes")

        xml += _tag("Web", f"https://www.dlsite.com/home/work/=/product_id/{work.product_id}.html")

    xml += _tag("PageCount", page_count)
    xml += "</ComicInfo>\n"
    return xml
