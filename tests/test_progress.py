"""Tests for reading progress persistence."""

from datetime import datetime, timezone
from pathlib import Path

from dlsite_opds.core.progress import ProgressStore


class TestProgressStore:
    def test_set_and_get(self, tmp_path: Path) -> None:
        store = ProgressStore(tmp_path / "p.json")
        store.set("RJ123456", 10)
        prog = store.get("RJ123456")
        assert prog is not None
        assert prog["last_read"] == 10
        assert "last_read_date" in prog

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        pf = tmp_path / "p.json"
        store1 = ProgressStore(pf)
        store1.set("BJ999999", 25)

        store2 = ProgressStore(pf)
        prog = store2.get("BJ999999")
        assert prog is not None
        assert prog["last_read"] == 25

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = ProgressStore(tmp_path / "p.json")
        assert store.get("NONEXISTENT") is None

    def test_get_all(self, tmp_path: Path) -> None:
        store = ProgressStore(tmp_path / "p.json")
        store.set("A", 1)
        store.set("B", 2)
        all_prog = store.get_all()
        assert len(all_prog) == 2
        assert all_prog["A"]["last_read"] == 1
        assert all_prog["B"]["last_read"] == 2

    def test_custom_date(self, tmp_path: Path) -> None:
        dt = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        store = ProgressStore(tmp_path / "p.json")
        store.set("RJ111111", 5, last_read_date=dt)
        prog = store.get("RJ111111")
        assert prog is not None
        assert prog["last_read_date"] == "2024-03-15T12:00:00Z"
