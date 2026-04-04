"""OPDS 1.2 Atom feed builders with OPDS-PSE namespace support."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from dlsite_async import Work

from ..core.play_client import PurchaseList

if TYPE_CHECKING:
    from .libraries import Library

# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/terms/"
OPDS_NS = "http://opds-spec.org/2010/catalog"
PSE_NS = "http://vaemendis.net/opds-pse/ns"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"

CATALOG_TYPE = "application/atom+xml;profile=opds-catalog"
NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"

ATOM_XML_TYPE = "application/atom+xml; charset=utf-8"

_FEED_OPEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<feed xmlns="http://www.w3.org/2005/Atom"\n'
    '      xmlns:dc="http://purl.org/dc/terms/"\n'
    '      xmlns:opds="http://opds-spec.org/2010/catalog"\n'
    '      xmlns:pse="http://vaemendis.net/opds-pse/ns"\n'
    '      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">\n'
)
_FEED_CLOSE = "</feed>\n"


def _e(text: str | None) -> str:
    """Escape text for XML content."""
    return escape(str(text)) if text else ""


def _a(text: str | None) -> str:
    """Escape text for use inside a double-quoted XML attribute value."""
    if text is None:
        return ""
    return escape(str(text), {'"': "&quot;"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(dt: datetime | None) -> str:
    if dt is None:
        return _now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_image_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return url


_IMAGE_MIMETYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
}


def _image_mimetype(url: str) -> str:
    dot = url.rfind(".")
    if dot != -1:
        ext = url[dot:].split("?", 1)[0].split("#", 1)[0].lower()
        return _IMAGE_MIMETYPES.get(ext, "image/jpeg")
    return "image/jpeg"


# ---------------------------------------------------------------------------
# Feed-level XML
# ---------------------------------------------------------------------------

def _feed_header(
    feed_id: str,
    title: str,
    self_href: str,
    start_href: str,
    self_type: str = NAVIGATION_TYPE,
) -> str:
    return (
        _FEED_OPEN
        + f"  <id>{_e(feed_id)}</id>\n"
        + f"  <title>{_e(title)}</title>\n"
        + f"  <updated>{_now()}</updated>\n"
        + "  <author><name>DLsite OPDS</name></author>\n"
        + f'  <link rel="self" href="{_a(self_href)}" type="{self_type}"/>\n'
        + f'  <link rel="start" href="{_a(start_href)}" type="{NAVIGATION_TYPE}"/>\n'
    )


# ---------------------------------------------------------------------------
# Entry fragment builders
# ---------------------------------------------------------------------------

def _entry_metadata(pid: str, title: str, updated: str) -> str:
    return (
        f"    <id>urn:dlsite:{_e(pid)}</id>\n"
        f"    <title>{_e(title)}</title>\n"
        f"    <updated>{updated}</updated>\n"
        f"    <dc:identifier>{_e(pid)}</dc:identifier>\n"
    )


def _entry_authors(work: Work) -> str:
    authors: list[str] = []
    if work.author:
        authors.extend(work.author)
    if work.circle:
        authors.append(work.circle)
    elif work.brand:
        authors.append(work.brand)
    if not authors and work.illustration:
        authors.extend(work.illustration)
    return "".join(
        f"    <author><name>{_e(name)}</name></author>\n"
        for name in dict.fromkeys(authors)
    )


def _entry_summary(work: Work) -> str:
    if not work.description:
        return ""
    return f'    <summary type="text">{_e(work.description)}</summary>\n'


def _entry_categories(work: Work) -> str:
    return "".join(
        f'    <category scheme="https://www.dlsite.com/genre"'
        f' term="{_a(g)}" label="{_a(g)}"/>\n'
        for g in work.genre or []
    )


def _entry_cover_links(work: Work, base_url: str) -> str:
    if not work.work_image:
        return ""
    cover_url = f"{base_url}/cover/{work.product_id}"
    mime = _image_mimetype(work.work_image)
    return (
        f'    <link rel="http://opds-spec.org/image"'
        f' href="{_a(cover_url)}" type="{mime}"/>\n'
        f'    <link rel="http://opds-spec.org/image/thumbnail"'
        f' href="{_a(cover_url)}" type="{mime}"/>\n'
    )


def _entry_alternate_link(work: Work) -> str:
    site = work.site_id or "maniax"
    pid = work.product_id
    web_url = f"https://www.dlsite.com/{site}/work/=/product_id/{pid}.html"
    return f'    <link rel="alternate" href="{_a(web_url)}" type="text/html"/>\n'


def _entry_download_link(
    pid: str,
    base_url: str,
    mime_type: str = "application/vnd.comicbook+zip",
) -> str:
    url = f"{base_url}/download/{pid}"
    return (
        f'    <link rel="http://opds-spec.org/acquisition/open-access"'
        f' href="{_a(url)}" type="{_a(mime_type)}"/>\n'
    )


def _entry_pse_link(
    pid: str,
    base_url: str,
    page_count: int | None,
    prog: dict[str, str | int] | None,
) -> str:
    if page_count is None or page_count <= 0:
        return ""
    pse_href = f"{base_url}/pse/{pid}?page={{pageNumber}}&width={{maxWidth}}"
    xml = (
        f'    <link rel="http://vaemendis.net/opds-pse/stream"'
        f' type="image/jpeg"'
        f' href="{_a(pse_href)}"'
        f' pse:count="{page_count}"'
    )
    if prog:
        xml += f' pse:lastRead="{prog["last_read"]}"'
        xml += f' pse:lastReadDate="{_a(str(prog["last_read_date"]))}"'
    xml += "/>\n"
    return xml


# ---------------------------------------------------------------------------
# Entry builder (orchestrator)
# ---------------------------------------------------------------------------

def _acquisition_link(
    pid: str,
    base_url: str,
    page_counts: dict[str, int],
    file_links: dict[str, str],
) -> str:
    """Choose the right acquisition link based on available content."""
    if pid in page_counts:
        return _entry_download_link(pid, base_url)
    if pid in file_links:
        return _entry_download_link(pid, base_url, file_links[pid])
    return _entry_download_link(pid, base_url)


def _work_entry(
    work: Work,
    purchase_date: datetime | None,
    base_url: str,
    page_counts: dict[str, int],
    progress: dict[str, dict[str, str | int]],
    file_links: dict[str, str] | None = None,
) -> str:
    pid = work.product_id
    updated = _dt(purchase_date or work.regist_date)
    pc = page_counts.get(pid)
    return "".join([
        "  <entry>\n",
        _entry_metadata(pid, work.work_name, updated),
        _entry_authors(work),
        _entry_summary(work),
        _entry_categories(work),
        _entry_cover_links(work, base_url),
        _acquisition_link(pid, base_url, page_counts,
                          file_links or {}),
        _entry_alternate_link(work),
        _entry_pse_link(pid, base_url, pc, progress.get(pid)),
        "  </entry>\n",
    ])


# ---------------------------------------------------------------------------
# Public feed builders
# ---------------------------------------------------------------------------

def build_navigation_feed(
    base_url: str,
    libraries: list[Library] | None = None,
) -> str:
    """Root OPDS navigation feed with entries for each content-type library."""
    xml = _feed_header(
        feed_id=f"{base_url}/opds",
        title="DLsite Library",
        self_href=f"{base_url}/opds",
        start_href=f"{base_url}/opds",
        self_type=NAVIGATION_TYPE,
    )

    xml += (
        "  <entry>\n"
        f"    <id>{_e(base_url)}/opds/purchases</id>\n"
        "    <title>All Purchases</title>\n"
        f"    <updated>{_now()}</updated>\n"
        '    <content type="text">All purchased works</content>\n'
        f'    <link rel="subsection" href="{_a(base_url)}/opds/purchases"'
        f' type="{ACQUISITION_TYPE}"/>\n'
        "  </entry>\n"
    )

    for lib in libraries or []:
        lib_href = f"{base_url}/opds/library/{lib.slug}"
        xml += (
            "  <entry>\n"
            f"    <id>{_e(base_url)}/opds/library/{_e(lib.slug)}</id>\n"
            f"    <title>{_e(lib.title)}</title>\n"
            f"    <updated>{_now()}</updated>\n"
            f'    <content type="text">{_e(lib.description)}</content>\n'
            f'    <link rel="subsection" href="{_a(lib_href)}"'
            f' type="{ACQUISITION_TYPE}"/>\n'
            "  </entry>\n"
        )

    xml += _FEED_CLOSE
    return xml


def build_purchases_feed(
    works: PurchaseList,
    page: int,
    page_size: int,
    total: int,
    base_url: str,
    page_counts: dict[str, int],
    progress: dict[str, dict[str, str | int]],
    *,
    title: str = "Purchases",
    feed_path: str = "/opds/purchases",
    file_links: dict[str, str] | None = None,
) -> str:
    """Paginated acquisition feed.

    *feed_path* is the path portion used for self/pagination links
    (e.g. ``/opds/purchases`` or ``/opds/library/manga``).
    """
    self_href = f"{base_url}{feed_path}?page={page}"
    xml = _feed_header(
        feed_id=self_href,
        title=title,
        self_href=self_href,
        start_href=f"{base_url}/opds",
        self_type=ACQUISITION_TYPE,
    )

    xml += (
        f"  <opensearch:totalResults>{total}</opensearch:totalResults>\n"
        f"  <opensearch:itemsPerPage>{page_size}</opensearch:itemsPerPage>\n"
        f"  <opensearch:startIndex>{(page - 1) * page_size + 1}</opensearch:startIndex>\n"
    )

    if (page * page_size) < total:
        next_href = f"{base_url}{feed_path}?page={page + 1}"
        xml += f'  <link rel="next" href="{_a(next_href)}" type="{ACQUISITION_TYPE}"/>\n'

    if page > 1:
        prev_href = f"{base_url}{feed_path}?page={page - 1}"
        xml += f'  <link rel="previous" href="{_a(prev_href)}" type="{ACQUISITION_TYPE}"/>\n'

    for work, purchase_date in works:
        xml += _work_entry(work, purchase_date, base_url, page_counts, progress,
                           file_links)

    xml += _FEED_CLOSE
    return xml
