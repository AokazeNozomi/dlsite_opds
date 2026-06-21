"""OPDS navigation and acquisition feed routes."""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from ..core.auth import AuthContext, get_auth
from ..core.config import Settings
from ..services.feeds import ATOM_XML_TYPE, build_chapter_feed, build_navigation_feed, build_purchases_feed
from ..services.libraries import LIBRARIES, filter_purchases, get_library, prepare_opds_purchases
from ..core.play_client import PurchaseList
from ..services.work_resolver import resolve_work_metadata

router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _base_url(request: Request) -> str:
    """Return the base URL for feed links.

    Uses the explicit ``DLSITE_OPDS_BASE_URL`` when set, otherwise derives
    it from the incoming request so that links always match the host the
    client actually connected to (avoids ``0.0.0.0`` in URLs).
    """
    cfg: Settings = request.app.state.settings
    if cfg.base_url:
        return cfg.base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


async def _build_feed_page(
    request: Request,
    auth: AuthContext,
    page: int,
    purchases: PurchaseList,
    *,
    title: str = "Purchases",
    feed_path: str = "/opds/purchases",
) -> Response:
    """Shared feed-building logic for purchases and per-library feeds."""
    cfg = _settings(request)
    base = _base_url(request)

    total = len(purchases)
    start = (page - 1) * cfg.page_size
    page_slice = purchases[start : start + cfg.page_size]

    info = await resolve_work_metadata(auth.client, page_slice)
    page_slice = [(w, d) for w, d in page_slice if info.has_content(w.product_id)]

    xml = build_purchases_feed(
        works=page_slice,
        page=page,
        page_size=cfg.page_size,
        total=total,
        base_url=base,
        page_counts=info.page_counts,
        progress=auth.progress.get_all(),
        title=title,
        feed_path=feed_path,
        file_links=info.file_links,
        chapter_counts=info.chapter_counts,
    )
    return Response(content=xml, media_type=ATOM_XML_TYPE)


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/opds", status_code=301)


@router.get("/{site}/work/{rest:path}")
async def dlsite_redirect(site: str, rest: str) -> RedirectResponse:
    """Redirect DLsite-style URLs to DLsite Play.

    OPDS readers sometimes resolve acquisition links as relative paths
    against the feed server.  Extract the product ID and send the user
    to the Play viewer instead.
    """
    m = re.search(r"(RJ|BJ|VJ)\d+", rest)
    pid = m.group(0) if m else rest
    return RedirectResponse(
        url=f"https://play.dlsite.com/work/{pid}/tree/",
        status_code=302,
    )


@router.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@router.get("/opds", response_class=Response)
async def opds_root(
    request: Request, auth: AuthContext = Depends(get_auth)
) -> Response:
    xml = build_navigation_feed(_base_url(request), libraries=LIBRARIES)
    return Response(content=xml, media_type=ATOM_XML_TYPE)


@router.get("/opds/purchases", response_class=Response)
async def opds_purchases(
    request: Request,
    auth: AuthContext = Depends(get_auth),
    page: int = Query(1, ge=1),
) -> Response:
    purchases = prepare_opds_purchases(await auth.client.get_purchases())
    return await _build_feed_page(request, auth, page, purchases)


@router.get("/opds/library/{slug}", response_class=Response)
async def opds_library(
    request: Request,
    slug: str,
    auth: AuthContext = Depends(get_auth),
    page: int = Query(1, ge=1),
) -> Response:
    """Per-content-type acquisition feed."""
    library = get_library(slug)
    if library is None:
        raise HTTPException(status_code=404, detail="Unknown library")

    purchases = prepare_opds_purchases(await auth.client.get_purchases())
    filtered = filter_purchases(purchases, library)
    return await _build_feed_page(
        request, auth, page, filtered,
        title=library.title,
        feed_path=f"/opds/library/{slug}",
    )


@router.get("/opds/work/{product_id}", response_class=Response)
async def opds_work_chapters(
    request: Request,
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> Response:
    """Navigation feed listing chapters for a multi-chapter work."""
    try:
        data = await auth.client.get_work_page_data(product_id)
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to fetch work data")

    if len(data.chapters) <= 1:
        raise HTTPException(
            status_code=404,
            detail="Work has no chapter navigation (single chapter or no pages)",
        )

    purchases = await auth.client.get_purchases()
    work = next((w for w, _ in purchases if w.product_id == product_id), None)
    if work is None:
        raise HTTPException(status_code=404, detail="Work not found in library")

    base = _base_url(request)
    xml = build_chapter_feed(
        work,
        data.chapters,
        base,
        auth.progress.get_all(),
    )
    return Response(content=xml, media_type=ATOM_XML_TYPE)
