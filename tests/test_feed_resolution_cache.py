"""Regression: cached ziptree data must repopulate file_links on refresh."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from dlsite_async.work import AgeCategory, Work

from dlsite_opds.core.play_client import WorkPageData
from dlsite_opds.services.chapters import ChapterGroup
from dlsite_opds.services.work_resolver import resolve_work_metadata


def _work(pid: str) -> Work:
    return Work(
        product_id=pid,
        site_id="comic",
        maker_id="BG01675",
        work_name="t",
        age_category=AgeCategory.ALL_AGES,
        circle="c",
        work_image="//img.dlsite.jp/test/x.jpg",
        regist_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_cached_zero_page_count_still_yields_file_links() -> None:
    """Voice/audio works cache page_count=0 with all_files; refresh must keep acq links."""
    client = MagicMock()
    pf = MagicMock()
    pf.is_epub_reflowable = False
    client.get_cached_work_page_data.return_value = WorkPageData(
        page_count=0,
        pages=[],
        all_files=[("folder/cover.jpg", pf)],
    )
    client.get_work_page_data = AsyncMock()

    page_slice = [(_work("RJ000001"), datetime.now(timezone.utc))]
    info = await resolve_work_metadata(client, page_slice)

    assert info.file_links["RJ000001"] == "image/jpeg"
    assert "RJ000001" not in info.page_counts
    assert info.chapter_counts == {}
    client.get_work_page_data.assert_not_called()


@pytest.mark.asyncio
async def test_cached_positive_page_count_skips_fetch() -> None:
    client = MagicMock()
    client.get_cached_work_page_data.return_value = WorkPageData(
        page_count=12,
        pages=[("p.jpg", MagicMock())],
        all_files=[],
    )
    client.get_work_page_data = AsyncMock()

    page_slice = [(_work("BJ000001"), None)]
    info = await resolve_work_metadata(client, page_slice)

    assert info.page_counts["BJ000001"] == 12
    client.get_work_page_data.assert_not_called()


@pytest.mark.asyncio
async def test_multi_chapter_work_sets_chapter_count() -> None:
    client = MagicMock()
    client.get_cached_work_page_data.return_value = WorkPageData(
        page_count=20,
        pages=[("a.jpg", MagicMock())] * 20,
        all_files=[],
        chapters=[
            ChapterGroup(key="img:ch1", title="ch1", pages=[("a.jpg", MagicMock())] * 10),
            ChapterGroup(key="img:ch2", title="ch2", pages=[("b.jpg", MagicMock())] * 10),
        ],
    )
    client.get_work_page_data = AsyncMock()

    page_slice = [(_work("BJ000002"), None)]
    info = await resolve_work_metadata(client, page_slice)

    assert info.chapter_counts["BJ000002"] == 2
    assert info.page_counts["BJ000002"] == 20
