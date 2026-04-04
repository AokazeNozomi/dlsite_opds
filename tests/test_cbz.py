"""Tests for ComicInfo.xml generation."""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from dlsite_async.work import AgeCategory, Work, WorkType

from dlsite_opds.services.cbz import build_comic_info


def _make_work(**kwargs: object) -> Work:
    defaults = dict(
        product_id="BJ370220",
        site_id="comic",
        maker_id="BG01675",
        work_name="Test Manga Vol.1",
        age_category=AgeCategory.ALL_AGES,
        circle="Test Circle",
        work_image="//img.dlsite.jp/test/BJ370220_img_main.jpg",
        regist_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        author=["Author A"],
        illustration=["Artist B"],
        genre=["Manga", "Comedy"],
        description="A test manga.",
        work_type=WorkType.MANGA,
    )
    defaults.update(kwargs)
    return Work(**defaults)  # type: ignore[arg-type]


class TestBuildComicInfo:
    def test_valid_xml(self) -> None:
        xml = build_comic_info(_make_work(), 42)
        root = ET.fromstring(xml)
        assert root.tag == "ComicInfo"

    def test_title(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Title") == "Test Manga Vol.1"

    def test_page_count(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 42))
        assert root.findtext("PageCount") == "42"

    def test_writer(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Writer") == "Author A"

    def test_writer_with_scenario(self) -> None:
        work = _make_work(author=["Author A"], scenario=["Scenario B"])
        root = ET.fromstring(build_comic_info(work, 10))
        assert root.findtext("Writer") == "Author A, Scenario B"

    def test_writer_deduplicates(self) -> None:
        work = _make_work(author=["Same"], scenario=["Same"])
        root = ET.fromstring(build_comic_info(work, 10))
        assert root.findtext("Writer") == "Same"

    def test_penciller(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Penciller") == "Artist B"

    def test_publisher_circle(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Publisher") == "Test Circle"

    def test_publisher_falls_back_to_brand(self) -> None:
        work = _make_work(circle=None, brand="Test Brand")
        root = ET.fromstring(build_comic_info(work, 10))
        assert root.findtext("Publisher") == "Test Brand"

    def test_genre(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Genre") == "Manga, Comedy"

    def test_summary(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Summary") == "A test manga."

    def test_date_fields(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Year") == "2024"
        assert root.findtext("Month") == "1"
        assert root.findtext("Day") == "15"

    def test_manga_yes_for_manga_type(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(work_type=WorkType.MANGA), 10))
        assert root.findtext("Manga") == "Yes"

    def test_manga_yes_for_webtoon(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(work_type=WorkType.WEBTOON), 10))
        assert root.findtext("Manga") == "Yes"

    def test_no_manga_tag_for_cg(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(work_type=WorkType.CG_ILLUSTRATIONS), 10))
        assert root.findtext("Manga") is None

    def test_web_link(self) -> None:
        root = ET.fromstring(build_comic_info(_make_work(), 10))
        assert root.findtext("Web") == "https://www.dlsite.com/home/work/=/product_id/BJ370220.html"

    def test_series_from_title_name(self) -> None:
        work = _make_work(title_name="My Series")
        root = ET.fromstring(build_comic_info(work, 10))
        assert root.findtext("Series") == "My Series"

    def test_none_work_still_has_page_count(self) -> None:
        root = ET.fromstring(build_comic_info(None, 25))
        assert root.findtext("PageCount") == "25"
        assert root.findtext("Title") is None

    def test_xml_escaping(self) -> None:
        work = _make_work(work_name='Title <with> "special" & chars')
        xml = build_comic_info(work, 5)
        root = ET.fromstring(xml)
        assert root.findtext("Title") == 'Title <with> "special" & chars'
