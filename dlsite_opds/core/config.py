"""Environment-based configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 2580
    base_url: str = ""
    data_dir: Path = field(default_factory=lambda: Path("~/.config/dlsite-opds"))
    cache_ttl: int = 300
    page_size: int = 30
    image_cache_ttl: int = 86400
    prefetch_ahead: int = 5
    cover_concurrency: int = 4
    cover_fetch_retries: int = 3
    cover_retry_delay: float = 0.5
    log_level: str = "INFO"

    @property
    def resolved_base_url(self) -> str:
        return self.base_url.rstrip("/") if self.base_url else f"http://{self.host}:{self.port}"

    @property
    def progress_dir(self) -> Path:
        return self.data_dir / "progress"

    @property
    def image_cache_dir(self) -> Path:
        return self.data_dir / "image_cache"


def load_settings() -> Settings:
    """Build a ``Settings`` from environment / ``.env`` file.

    Side effects (dotenv loading, directory creation) happen here rather
    than at module import time.
    """
    load_dotenv()

    data_dir = Path(
        os.getenv("DLSITE_OPDS_DATA_DIR", "~/.config/dlsite-opds")
    ).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        host=os.getenv("DLSITE_OPDS_HOST", "127.0.0.1"),
        port=int(os.getenv("DLSITE_OPDS_PORT", "2580")),
        base_url=os.getenv("DLSITE_OPDS_BASE_URL", ""),
        data_dir=data_dir,
        cache_ttl=int(os.getenv("DLSITE_OPDS_CACHE_TTL", "300")),
        page_size=int(os.getenv("DLSITE_OPDS_PAGE_SIZE", "30")),
        image_cache_ttl=int(os.getenv("DLSITE_OPDS_IMAGE_CACHE_TTL", "86400")),
        prefetch_ahead=int(os.getenv("DLSITE_OPDS_PREFETCH_AHEAD", "5")),
        cover_concurrency=int(os.getenv("DLSITE_OPDS_COVER_CONCURRENCY", "4")),
        cover_fetch_retries=int(os.getenv("DLSITE_OPDS_COVER_FETCH_RETRIES", "3")),
        cover_retry_delay=float(os.getenv("DLSITE_OPDS_COVER_RETRY_DELAY", "0.5")),
        log_level=os.getenv("DLSITE_OPDS_LOG_LEVEL", "INFO").upper(),
    )
