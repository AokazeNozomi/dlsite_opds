"""Reading progress persistence (OPDS-PSE v1.2 lastRead / lastReadDate)."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ProgressStore:
    """JSON-file backed per-work reading progress.

    ``last_read`` values are **1-based** page numbers, matching the
    OPDS-PSE v1.2 ``pse:lastRead`` semantics (page numbering starts at 1
    for progression, even though ``{pageNumber}`` in the stream href is
    0-based).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict[str, str | int]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text("utf-8"))
            except Exception:
                logger.warning("Failed to load progress file; starting fresh")
                self._data = {}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(self._path)

    def get(self, product_id: str) -> dict[str, str | int] | None:
        return self._data.get(product_id)

    def get_all(self) -> dict[str, dict[str, str | int]]:
        return dict(self._data)

    def set(
        self,
        product_id: str,
        last_read: int,
        last_read_date: datetime | None = None,
    ) -> None:
        if last_read_date is None:
            last_read_date = datetime.now(timezone.utc)
        self._data[product_id] = {
            "last_read": last_read,
            "last_read_date": last_read_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._save()


class ProgressManager:
    """Returns per-user :class:`ProgressStore` instances, one JSON file each."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, ProgressStore] = {}

    def for_user(self, login_id: str) -> ProgressStore:
        if login_id not in self._stores:
            path = self._dir / f"{login_id}.json"
            self._stores[login_id] = ProgressStore(path)
        return self._stores[login_id]
