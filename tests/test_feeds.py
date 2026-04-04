"""Tests for OPDS feed XML structure and OPDS-PSE link compliance."""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
from dlsite_async.work import AgeCategory, Work, WorkType

from dlsite_opds.services.feeds import (
    ACQUISITION_TYPE,
    ATOM_NS,
    DC_NS,
    NAVIGATION_TYPE,
    OPENSEARCH_NS,
    PSE_NS,
    build_navigation_feed,
    build_purchases_feed,
)
from dlsite_opds.services.libraries import (
    LIBRARIES,
    Library,
    filter_purchases,
    get_library,
    prepare_opds_purchases,
)

ATOM = f"{{{ATOM_NS}}}"
DC = f"{{{DC_NS}}}"
PSE = f"{{{PSE_NS}}}"
OS = f"{{{OPENSEARCH_NS}}}"


def _make_work(
    product_id: str = "BJ370220",
    work_name: str = "Test Manga Vol.1",
    **kwargs: object,
) -> Work:
    defaults = dict(
        product_id=product_id,
        site_id="comic",
        maker_id="BG01675",
        work_name=work_name,
        age_category=AgeCategory.ALL_AGES,
        circle="Test Circle",
        work_image="//img.dlsite.jp/test/BJ370220_img_main.jpg",
        regist_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        author=["Author A", "Author B"],
        genre=["Manga", "Comedy"],
    )
    defaults.update(kwargs)
    return Work(**defaults)  # type: ignore[arg-type]


BASE = "http://localhost:2580"


class TestNavigationFeed:
    def test_valid_atom_with_namespaces(self) -> None:
        xml = build_navigation_feed(BASE)
        root = ET.fromstring(xml)
        assert root.tag == f"{ATOM}feed"

    def test_has_self_and_start_links(self) -> None:
        xml = build_navigation_feed(BASE)
        root = ET.fromstring(xml)
        links = root.findall(f"{ATOM}link")
        rels = {link.get("rel") for link in links}
        assert "self" in rels
        assert "start" in rels

    def test_self_link_has_navigation_kind(self) -> None:
        xml = build_navigation_feed(BASE)
        root = ET.fromstring(xml)
        self_link = [
            l for l in root.findall(f"{ATOM}link") if l.get("rel") == "self"
        ][0]
        assert self_link.get("type") == NAVIGATION_TYPE

    def test_has_purchases_subsection_entry(self) -> None:
        xml = build_navigation_feed(BASE)
        root = ET.fromstring(xml)
        entries = root.findall(f"{ATOM}entry")
        assert len(entries) >= 1

        link = entries[0].find(f"{ATOM}link")
        assert link is not None
        assert link.get("rel") == "subsection"
        assert "/opds/purchases" in (link.get("href") or "")
        assert link.get("type") == ACQUISITION_TYPE

    def test_has_library_entries_when_provided(self) -> None:
        xml = build_navigation_feed(BASE, libraries=LIBRARIES)
        root = ET.fromstring(xml)
        entries = root.findall(f"{ATOM}entry")
        assert len(entries) == 1 + len(LIBRARIES)

        first_link = entries[0].find(f"{ATOM}link")
        assert first_link is not None
        assert "/opds/purchases" in (first_link.get("href") or "")

        for entry, lib in zip(entries[1:], LIBRARIES):
            assert entry.findtext(f"{ATOM}title") == lib.title
            link = entry.find(f"{ATOM}link")
            assert link is not None
            assert link.get("rel") == "subsection"
            assert f"/opds/library/{lib.slug}" in (link.get("href") or "")
            assert link.get("type") == ACQUISITION_TYPE


class TestPurchasesFeed:
    def _build(
        self,
        works: list[tuple[Work, datetime | None]] | None = None,
        page_counts: dict[str, int] | None = None,
        progress: dict[str, dict[str, str | int]] | None = None,
    ) -> ET.Element:
        if works is None:
            w = _make_work()
            works = [(w, datetime(2024, 6, 1, tzinfo=timezone.utc))]
        xml = build_purchases_feed(
            works=works,
            page=1,
            page_size=30,
            total=len(works),
            base_url=BASE,
            page_counts=page_counts or {},
            progress=progress or {},
        )
        return ET.fromstring(xml)

    def test_entry_has_basic_metadata(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        assert entry.findtext(f"{ATOM}title") == "Test Manga Vol.1"
        assert entry.findtext(f"{DC}identifier") == "BJ370220"

    def test_entry_has_authors(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        authors = [
            a.findtext(f"{ATOM}name")
            for a in entry.findall(f"{ATOM}author")
        ]
        assert "Author A" in authors
        assert "Test Circle" in authors

    def test_entry_has_cover_image_links(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        links = entry.findall(f"{ATOM}link")
        image_rels = [
            l.get("rel")
            for l in links
            if "opds-spec.org/image" in (l.get("rel") or "")
        ]
        assert len(image_rels) == 2  # image + thumbnail

    def test_cover_urls_use_proxy(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        links = entry.findall(f"{ATOM}link")
        for link in links:
            if "opds-spec.org/image" in (link.get("rel") or ""):
                href = link.get("href") or ""
                assert "/cover/BJ370220" in href, f"Cover should use proxy URL: {href}"

    def test_entry_has_alternate_link(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        links = entry.findall(f"{ATOM}link")
        alt = [l for l in links if l.get("rel") == "alternate"]
        assert len(alt) == 1
        assert "dlsite.com" in (alt[0].get("href") or "")

    def test_no_pse_link_without_page_count(self) -> None:
        w = _make_work(work_type=WorkType.MANGA)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={},
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        pse_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ]
        assert len(pse_links) == 0

    def test_pse_link_present_with_page_count(self) -> None:
        w = _make_work(work_type=WorkType.MANGA)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={"BJ370220": 42},
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        pse_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ]
        assert len(pse_links) == 1

    def test_pse_link_attributes(self) -> None:
        w = _make_work(work_type=WorkType.MANGA)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={"BJ370220": 42},
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        link = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ][0]

        assert link.get("rel") == "http://vaemendis.net/opds-pse/stream"
        assert link.get("type") == "image/jpeg"

        href = link.get("href") or ""
        assert "{pageNumber}" in href
        assert "{maxWidth}" in href
        assert "/pse/BJ370220" in href

        assert link.get(f"{PSE}count") == "42"

    def test_pse_link_with_progress(self) -> None:
        w = _make_work(work_type=WorkType.MANGA)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={"BJ370220": 42},
            progress={
                "BJ370220": {
                    "last_read": 15,
                    "last_read_date": "2024-06-15T10:30:00Z",
                }
            },
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        link = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ][0]
        assert link.get(f"{PSE}lastRead") == "15"
        assert link.get(f"{PSE}lastReadDate") == "2024-06-15T10:30:00Z"

    def test_pse_link_present_for_voice_asmr_with_pages(self) -> None:
        w = _make_work(product_id="RJ000001", work_type=WorkType.VOICE_ASMR)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={"RJ000001": 5},
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        pse_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ]
        assert len(pse_links) == 1

    def test_pse_link_present_for_games_with_pages(self) -> None:
        w = _make_work(product_id="RJ000002", work_type=WorkType.ROLE_PLAYING)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={"RJ000002": 10},
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        pse_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ]
        assert len(pse_links) == 1

    def test_pse_link_allowed_for_unknown_work_type(self) -> None:
        w = _make_work(work_type=None)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page_counts={"BJ370220": 42},
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        pse_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if "vaemendis.net/opds-pse/stream" in (l.get("rel") or "")
        ]
        assert len(pse_links) == 1

    def test_all_types_get_acquisition_link(self) -> None:
        w = _make_work(product_id="RJ000001", work_type=WorkType.VIDEO)
        root = self._build(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
        )
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        links = entry.findall(f"{ATOM}link")
        acq_links = [
            l for l in links
            if (l.get("rel") or "").startswith("http://opds-spec.org/acquisition")
        ]
        assert len(acq_links) == 1
        assert "/download/RJ000001" in (acq_links[0].get("href") or "")

    def test_pagination_next_link(self) -> None:
        works = [
            (_make_work(product_id=f"BJ{i:06d}"), None)
            for i in range(35)
        ]
        xml = build_purchases_feed(
            works=works[:30],
            page=1,
            page_size=30,
            total=35,
            base_url=BASE,
            page_counts={},
            progress={},
        )
        root = ET.fromstring(xml)
        next_links = [
            l
            for l in root.findall(f"{ATOM}link")
            if l.get("rel") == "next"
        ]
        assert len(next_links) == 1
        assert "page=2" in (next_links[0].get("href") or "")

    def test_no_next_link_on_last_page(self) -> None:
        works = [(_make_work(), None)]
        xml = build_purchases_feed(
            works=works,
            page=1,
            page_size=30,
            total=1,
            base_url=BASE,
            page_counts={},
            progress={},
        )
        root = ET.fromstring(xml)
        next_links = [
            l
            for l in root.findall(f"{ATOM}link")
            if l.get("rel") == "next"
        ]
        assert len(next_links) == 0

    def test_categories_in_entry(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        cats = entry.findall(f"{ATOM}category")
        labels = {c.get("label") for c in cats}
        assert "Manga" in labels
        assert "Comedy" in labels
        for cat in cats:
            assert cat.get("scheme") == "https://www.dlsite.com/genre"

    def test_self_link_has_acquisition_kind(self) -> None:
        root = self._build()
        self_link = [
            l for l in root.findall(f"{ATOM}link") if l.get("rel") == "self"
        ][0]
        assert self_link.get("type") == ACQUISITION_TYPE

    def test_entry_has_download_acquisition_link(self) -> None:
        root = self._build()
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        acq_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if l.get("rel") == "http://opds-spec.org/acquisition/open-access"
        ]
        assert len(acq_links) == 1
        href = acq_links[0].get("href") or ""
        assert "/download/BJ370220" in href
        assert acq_links[0].get("type") == "application/vnd.comicbook+zip"

    def test_pdf_acquisition_link_via_file_links(self) -> None:
        w = _make_work(product_id="RJ100001", work_type=WorkType.MANGA)
        xml = build_purchases_feed(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page=1,
            page_size=30,
            total=1,
            base_url=BASE,
            page_counts={},
            progress={},
            file_links={"RJ100001": "application/pdf"},
        )
        root = ET.fromstring(xml)
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        acq_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if l.get("rel") == "http://opds-spec.org/acquisition/open-access"
        ]
        assert len(acq_links) == 1
        href = acq_links[0].get("href") or ""
        assert "/download/RJ100001" in href
        assert acq_links[0].get("type") == "application/pdf"

    def test_non_pse_type_with_file_link_gets_acquisition(self) -> None:
        w = _make_work(product_id="RJ200001", work_type=WorkType.VIDEO)
        xml = build_purchases_feed(
            works=[(w, datetime(2024, 6, 1, tzinfo=timezone.utc))],
            page=1,
            page_size=30,
            total=1,
            base_url=BASE,
            page_counts={},
            progress={},
            file_links={"RJ200001": "application/pdf"},
        )
        root = ET.fromstring(xml)
        entry = root.find(f"{ATOM}entry")
        assert entry is not None
        acq_links = [
            l
            for l in entry.findall(f"{ATOM}link")
            if l.get("rel") == "http://opds-spec.org/acquisition/open-access"
        ]
        assert len(acq_links) == 1
        assert acq_links[0].get("type") == "application/pdf"

    def test_opensearch_elements(self) -> None:
        works = [
            (_make_work(product_id=f"BJ{i:06d}"), None)
            for i in range(35)
        ]
        xml = build_purchases_feed(
            works=works[:30],
            page=1,
            page_size=30,
            total=35,
            base_url=BASE,
            page_counts={},
            progress={},
        )
        root = ET.fromstring(xml)
        assert root.findtext(f"{OS}totalResults") == "35"
        assert root.findtext(f"{OS}itemsPerPage") == "30"
        assert root.findtext(f"{OS}startIndex") == "1"

    def test_opensearch_start_index_page_2(self) -> None:
        works = [(_make_work(), None)]
        xml = build_purchases_feed(
            works=works,
            page=2,
            page_size=30,
            total=35,
            base_url=BASE,
            page_counts={},
            progress={},
        )
        root = ET.fromstring(xml)
        assert root.findtext(f"{OS}startIndex") == "31"

    def test_pagination_previous_link(self) -> None:
        works = [(_make_work(), None)]
        xml = build_purchases_feed(
            works=works,
            page=2,
            page_size=30,
            total=35,
            base_url=BASE,
            page_counts={},
            progress={},
        )
        root = ET.fromstring(xml)
        prev_links = [
            l
            for l in root.findall(f"{ATOM}link")
            if l.get("rel") == "previous"
        ]
        assert len(prev_links) == 1
        assert "page=1" in (prev_links[0].get("href") or "")

    def test_no_previous_link_on_first_page(self) -> None:
        root = self._build()
        prev_links = [
            l
            for l in root.findall(f"{ATOM}link")
            if l.get("rel") == "previous"
        ]
        assert len(prev_links) == 0

    def test_custom_title_and_feed_path(self) -> None:
        works = [(_make_work(), None)]
        xml = build_purchases_feed(
            works=works,
            page=1,
            page_size=30,
            total=1,
            base_url=BASE,
            page_counts={},
            progress={},
            title="Manga / Doujinshi",
            feed_path="/opds/library/manga",
        )
        root = ET.fromstring(xml)
        assert root.findtext(f"{ATOM}title") == "Manga / Doujinshi"
        self_link = [
            l for l in root.findall(f"{ATOM}link") if l.get("rel") == "self"
        ][0]
        assert "/opds/library/manga" in (self_link.get("href") or "")

    def test_custom_feed_path_pagination(self) -> None:
        works = [
            (_make_work(product_id=f"BJ{i:06d}"), None) for i in range(35)
        ]
        xml = build_purchases_feed(
            works=works[:30],
            page=1,
            page_size=30,
            total=35,
            base_url=BASE,
            page_counts={},
            progress={},
            feed_path="/opds/library/manga",
        )
        root = ET.fromstring(xml)
        next_links = [
            l for l in root.findall(f"{ATOM}link") if l.get("rel") == "next"
        ]
        assert len(next_links) == 1
        href = next_links[0].get("href") or ""
        assert "/opds/library/manga" in href
        assert "page=2" in href


class TestLibraries:
    def test_get_library_valid_slug(self) -> None:
        lib = get_library("manga")
        assert lib is not None
        assert lib.title == "Manga / Doujinshi"

    def test_get_library_unknown_slug(self) -> None:
        assert get_library("nonexistent") is None

    def test_all_slugs_unique(self) -> None:
        slugs = [lib.slug for lib in LIBRARIES]
        assert len(slugs) == len(set(slugs))

    def test_filter_by_work_type(self) -> None:
        manga = _make_work(
            product_id="BJ000001", work_type=WorkType.MANGA
        )
        asmr = _make_work(
            product_id="RJ000001", work_type=WorkType.VOICE_ASMR
        )
        purchases = [
            (manga, datetime(2024, 1, 1, tzinfo=timezone.utc)),
            (asmr, datetime(2024, 2, 1, tzinfo=timezone.utc)),
        ]
        lib = get_library("manga")
        assert lib is not None
        filtered = filter_purchases(purchases, lib)
        assert len(filtered) == 1
        assert filtered[0][0].product_id == "BJ000001"

    def test_prepare_opds_purchases_keeps_all_typed_works(self) -> None:
        manga = _make_work(product_id="BJ000001", work_type=WorkType.MANGA)
        asmr = _make_work(product_id="RJ000001", work_type=WorkType.VOICE_ASMR)
        novel = _make_work(product_id="BJ000002", work_type=WorkType.NOVEL)
        game = _make_work(product_id="RJ000002", work_type=WorkType.ROLE_PLAYING)
        untyped = _make_work(product_id="RJ000003", work_type=None)
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        purchases = [(manga, dt), (asmr, dt), (novel, dt), (game, dt), (untyped, dt)]
        result = prepare_opds_purchases(purchases)
        pids = [w.product_id for w, _ in result]
        assert pids == ["BJ000001", "RJ000001", "BJ000002", "RJ000002"]

    def test_filter_catchall_other_library(self) -> None:
        manga = _make_work(product_id="BJ000001", work_type=WorkType.MANGA)
        asmr = _make_work(product_id="RJ000001", work_type=WorkType.VOICE_ASMR)
        game = _make_work(product_id="RJ000002", work_type=WorkType.ROLE_PLAYING)
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        purchases = [(manga, dt), (asmr, dt), (game, dt)]
        lib = get_library("other")
        assert lib is not None
        assert lib.is_catchall
        filtered = filter_purchases(purchases, lib)
        pids = [w.product_id for w, _ in filtered]
        assert pids == ["RJ000001", "RJ000002"]

    def test_no_work_type_in_multiple_libraries(self) -> None:
        seen: dict[WorkType, str] = {}
        for lib in LIBRARIES:
            if lib.is_catchall:
                continue
            for wt in lib.work_types:
                assert wt not in seen, (
                    f"{wt} in both '{seen[wt]}' and '{lib.slug}'"
                )
                seen[wt] = lib.slug


class TestDumpXml:
    """Dump real OPDS XML from a live DLsite account for manual inspection.

    Requires ``DLSITE_LOGIN_ID`` / ``DLSITE_PASSWORD`` (env or ``.env``).
    Run with::

        pytest tests/test_feeds.py::TestDumpXml -s

    Produces one XML file per library plus the navigation and all-purchases
    feeds.  Output paths are printed to stdout.
    """

    @staticmethod
    def _pretty(xml_str: str) -> str:
        ET.register_namespace("", ATOM_NS)
        ET.register_namespace("dc", DC_NS)
        ET.register_namespace("pse", PSE_NS)
        ET.register_namespace("opensearch", OPENSEARCH_NS)
        root = ET.fromstring(xml_str)
        ET.indent(root)
        return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"

    @staticmethod
    def _write(dest, content: str) -> None:
        dest.write_text(content, encoding="utf-8")
        print(f"  -> {dest}")

    def test_dump_navigation_feed(self, tmp_path) -> None:
        xml = build_navigation_feed(BASE, libraries=LIBRARIES)
        self._write(tmp_path / "navigation_feed.xml", self._pretty(xml))

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_dump_all_feeds(self, tmp_path) -> None:
        import asyncio
        import os

        from dotenv import load_dotenv

        load_dotenv()
        if not (os.getenv("DLSITE_LOGIN_ID") and os.getenv("DLSITE_PASSWORD")):
            pytest.skip("DLSITE_LOGIN_ID / DLSITE_PASSWORD not set")

        from dlsite_opds.core.play_client import DlsiteClient

        client = DlsiteClient(
            login_id=os.environ["DLSITE_LOGIN_ID"],
            password=os.environ["DLSITE_PASSWORD"],
        )
        try:
            await client.initialize()
            purchases = await client.get_purchases()

            page_counts: dict[str, int] = {}
            sem = asyncio.Semaphore(5)

            async def _fetch(pid: str) -> None:
                async with sem:
                    try:
                        data = await client.get_work_page_data(pid)
                        if data.page_count:
                            page_counts[pid] = data.page_count
                    except Exception:
                        pass

            all_pids = [w.product_id for w, _ in purchases]
            await asyncio.gather(*(_fetch(pid) for pid in all_pids))

            # All purchases
            xml = build_purchases_feed(
                works=purchases[:30],
                page=1,
                page_size=30,
                total=len(purchases),
                base_url=BASE,
                page_counts=page_counts,
                progress={},
            )
            self._write(tmp_path / "purchases.xml", self._pretty(xml))

            # Per-library feeds
            for lib in LIBRARIES:
                filtered = filter_purchases(purchases, lib)
                if not filtered:
                    continue
                xml = build_purchases_feed(
                    works=filtered[:30],
                    page=1,
                    page_size=30,
                    total=len(filtered),
                    base_url=BASE,
                    page_counts=page_counts,
                    progress={},
                    title=lib.title,
                    feed_path=f"/opds/library/{lib.slug}",
                )
                self._write(
                    tmp_path / f"library_{lib.slug}.xml",
                    self._pretty(xml),
                )
        finally:
            await client.close()
