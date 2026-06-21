"""End-to-end tests against the live DLsite Play API.

Require real credentials via ``DLSITE_LOGIN_ID`` / ``DLSITE_PASSWORD``
environment variables (or ``.env``).  Run with::

    pytest tests/test_e2e.py -v

Skipped automatically when credentials are absent.
"""

import asyncio
import logging
import os
import xml.etree.ElementTree as ET

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv()

from dlsite_opds.services.feeds import ATOM_NS, DC_NS

log = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (os.getenv("DLSITE_LOGIN_ID") and os.getenv("DLSITE_PASSWORD")),
        reason="DLSITE_LOGIN_ID / DLSITE_PASSWORD not set",
    ),
]

ATOM = f"{{{ATOM_NS}}}"
DC = f"{{{DC_NS}}}"


# ---------------------------------------------------------------------------
# Direct DlsiteClient tests (single shared session per class)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="class", loop_scope="class")
async def play():
    """A single DlsiteClient session shared across the whole class."""
    from dlsite_opds.core.play_client import DlsiteClient

    c = DlsiteClient(
        login_id=os.getenv("DLSITE_LOGIN_ID", ""),
        password=os.getenv("DLSITE_PASSWORD", ""),
    )
    await c.initialize()
    log.info("DlsiteClient session opened")
    yield c
    await c.close()
    log.info("DlsiteClient session closed")


@pytest.mark.asyncio(loop_scope="class")
class TestPlayClient:
    """Verify login, purchase listing, and page download via DlsiteClient."""

    async def test_login_and_list_purchases(self, play) -> None:
        purchases = await play.get_purchases()
        log.info("Fetched %d purchases", len(purchases))
        assert len(purchases) > 0, "Account has no purchases"
        work, purchase_date = purchases[0]
        log.info(
            "First purchase: %s — %s (bought %s)",
            work.product_id,
            work.work_name,
            purchase_date,
        )
        assert work.product_id
        assert work.work_name

    async def test_get_work_page_data(self, play) -> None:
        purchases = await play.get_purchases()
        for work, _ in purchases[:5]:
            try:
                data = await play.get_work_page_data(work.product_id)
                log.info(
                    "%s: %d pages, token expires %s",
                    work.product_id,
                    data.page_count,
                    data.token.expires_at if data.token else "N/A",
                )
                if data.page_count > 0:
                    assert data.pages
                    assert data.token is not None
                    return
            except Exception as exc:
                log.info("%s: skipped (%s)", work.product_id, exc)
                continue
        pytest.fail("No works with page data found in first 5 purchases")

    async def test_pdf_work_pages_extracted(self, play) -> None:
        """PDF works should have their pages expanded into image entries."""
        product_id = "RJ01459324"
        data = await play.get_work_page_data(product_id)
        log.info(
            "%s: page_count=%d, all_files=%d",
            product_id, data.page_count, len(data.all_files),
        )
        assert data.page_count > 0, (
            f"{product_id} is a PDF work — pages should be expanded"
        )
        log.info(
            "%s: expanded to %d pages", product_id, data.page_count
        )

    async def test_download_pdf_work_page(self, play) -> None:
        """Download a single page from a PDF work via the normal image path."""
        product_id = "RJ01459324"
        image_bytes, pf = await play.download_page_image(product_id, 0)
        log.info(
            "%s page 0: %d bytes, type=%s",
            product_id, len(image_bytes), pf.type,
        )
        assert len(image_bytes) > 100, "Page image suspiciously small"

    async def test_download_first_page(self, play) -> None:
        purchases = await play.get_purchases()
        for work, _ in purchases[:5]:
            try:
                data = await play.get_work_page_data(work.product_id)
                if data.page_count == 0:
                    log.info("%s: 0 pages, skipping", work.product_id)
                    continue
                image_bytes, pf = await play.download_page_image(
                    work.product_id, 0
                )
                log.info(
                    "%s: downloaded page 0 — %d bytes, type=%s, crypt=%s",
                    work.product_id,
                    len(image_bytes),
                    pf.type,
                    getattr(pf, "crypt", False),
                )
                assert len(image_bytes) > 100, "Image suspiciously small"
                return
            except Exception as exc:
                log.info("%s: skipped (%s)", work.product_id, exc)
                continue
        pytest.fail("No works with downloadable image pages in first 5 purchases")


# ---------------------------------------------------------------------------
# HTTP endpoint tests via ASGI transport (lifespan-managed app)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="class", loop_scope="class")
async def ac():
    """ASGI test client with manually initialised app state.

    ``httpx.ASGITransport`` does not trigger FastAPI lifespan events,
    so we populate ``app.state`` ourselves.
    """
    from concurrent.futures import ThreadPoolExecutor

    import aiohttp

    from dlsite_opds.core.auth import ClientPool, SourceImageLRU
    from dlsite_opds.app import app
    from dlsite_opds.core.config import load_settings
    from dlsite_opds.services.image_cache import ImageCache
    from dlsite_opds.core.progress import ProgressManager

    cfg = load_settings()
    app.state.settings = cfg
    app.state.pool = ClientPool(cfg.cache_ttl)
    app.state.progress_manager = ProgressManager(cfg.progress_dir)
    app.state.image_cache = ImageCache(cfg.image_cache_dir, cfg.image_cache_ttl)
    app.state.source_cache = SourceImageLRU()
    app.state.image_executor = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="img-test",
    )
    app.state.prefetch_inflight = set()
    app.state.cover_semaphore = asyncio.Semaphore(cfg.cover_concurrency)
    app.state.cover_session = aiohttp.ClientSession()
    app.state.cover_cache = {}
    log.info("HTTP test client ready (app.state initialised)")

    login_id = os.getenv("DLSITE_LOGIN_ID", "")
    password = os.getenv("DLSITE_PASSWORD", "")

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        auth=(login_id, password),
    ) as c:
        yield c

    app.state.image_executor.shutdown(wait=False)
    await app.state.cover_session.close()
    await app.state.pool.close_all()
    log.info("HTTP test client closed")


@pytest.mark.asyncio(loop_scope="class")
class TestHTTPEndpoints:
    """Full HTTP stack tests through FastAPI."""

    async def test_healthz(self, ac: httpx.AsyncClient) -> None:
        r = await ac.get("/healthz")
        log.info("GET /healthz → %d %s", r.status_code, r.text)
        assert r.status_code == 200
        assert r.text == "ok"

    async def test_opds_root_is_valid_atom(self, ac: httpx.AsyncClient) -> None:
        r = await ac.get("/opds")
        log.info(
            "GET /opds → %d, content-type=%s, body=%d bytes",
            r.status_code,
            r.headers.get("content-type"),
            len(r.content),
        )
        assert r.status_code == 200
        assert "application/atom+xml" in r.headers["content-type"]
        root = ET.fromstring(r.text)
        assert root.tag == f"{ATOM}feed"

    async def test_purchases_feed_has_entries(self, ac: httpx.AsyncClient) -> None:
        r = await ac.get("/opds/purchases")
        assert r.status_code == 200
        root = ET.fromstring(r.text)
        entries = root.findall(f"{ATOM}entry")
        log.info(
            "GET /opds/purchases → %d entries on page 1", len(entries)
        )

        assert len(entries) > 0, "Purchases feed should contain entries"

        entry = entries[0]
        title = entry.findtext(f"{ATOM}title")
        pid = entry.findtext(f"{DC}identifier")
        log.info("First entry: %s — %s", pid, title)
        assert title, "Entry missing title"
        assert pid, "Entry missing dc:identifier"

    async def test_pse_page_returns_jpeg(self, ac: httpx.AsyncClient) -> None:
        r = await ac.get("/opds/purchases")
        root = ET.fromstring(r.text)
        entries = root.findall(f"{ATOM}entry")

        for entry in entries[:5]:
            pid = entry.findtext(f"{DC}identifier")
            if not pid:
                continue
            r = await ac.get(f"/pse/{pid}", params={"page": 0})
            log.info(
                "GET /pse/%s?page=0 → %d, %d bytes",
                pid,
                r.status_code,
                len(r.content),
            )
            if r.status_code == 200:
                assert r.headers["content-type"] == "image/jpeg"
                assert r.content[:2] == b"\xff\xd8", "Response is not a valid JPEG"
                assert len(r.content) > 100, "JPEG body suspiciously small"
                return

        pytest.fail("No downloadable image pages found in first 5 purchases")

    async def test_pse_page_with_width_resize(self, ac: httpx.AsyncClient) -> None:
        r = await ac.get("/opds/purchases")
        root = ET.fromstring(r.text)
        entries = root.findall(f"{ATOM}entry")

        for entry in entries[:5]:
            pid = entry.findtext(f"{DC}identifier")
            if not pid:
                continue
            full = await ac.get(f"/pse/{pid}", params={"page": 0})
            if full.status_code != 200:
                log.info("GET /pse/%s?page=0 → %d, skipping", pid, full.status_code)
                continue
            resized = await ac.get(
                f"/pse/{pid}", params={"page": 0, "width": 800}
            )
            log.info(
                "GET /pse/%s?page=0&width=800 → %d, %d bytes (full was %d bytes)",
                pid,
                resized.status_code,
                len(resized.content),
                len(full.content),
            )
            assert resized.status_code == 200
            assert resized.headers["content-type"] == "image/jpeg"
            assert resized.content[:2] == b"\xff\xd8"
            assert len(resized.content) <= len(full.content), (
                "Resized image should not be larger than the original"
            )
            return

        pytest.fail("No downloadable pages found for width resize test")

    async def test_pse_invalid_page_returns_404(self, ac: httpx.AsyncClient) -> None:
        r = await ac.get("/opds/purchases")
        root = ET.fromstring(r.text)
        entries = root.findall(f"{ATOM}entry")

        for entry in entries[:5]:
            pid = entry.findtext(f"{DC}identifier")
            if not pid:
                continue
            probe = await ac.get(f"/pse/{pid}", params={"page": 0})
            if probe.status_code != 200:
                continue
            r = await ac.get(f"/pse/{pid}", params={"page": 999999})
            log.info(
                "GET /pse/%s?page=999999 → %d", pid, r.status_code
            )
            assert r.status_code == 404
            return

        pytest.fail("No works available to test invalid page index")

    async def test_download_pdf_work_cbz(self, ac: httpx.AsyncClient) -> None:
        """GET /download/RJ01459324 should return a CBZ built from PDF pages."""
        r = await ac.get("/download/RJ01459324", timeout=120)
        log.info(
            "GET /download/RJ01459324 → %d, content-type=%s, %d bytes",
            r.status_code,
            r.headers.get("content-type"),
            len(r.content),
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        assert "comicbook+zip" in r.headers.get("content-type", ""), (
            f"Expected CBZ MIME type, got {r.headers.get('content-type')}"
        )
        assert len(r.content) > 1000, "CBZ body suspiciously small"

    async def test_pse_pdf_work_page(self, ac: httpx.AsyncClient) -> None:
        """GET /pse/RJ01459324?page=0 should return a JPEG page."""
        r = await ac.get("/pse/RJ01459324", params={"page": 0})
        log.info(
            "GET /pse/RJ01459324?page=0 → %d, %d bytes",
            r.status_code, len(r.content),
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:2] == b"\xff\xd8"
        assert len(r.content) > 100
