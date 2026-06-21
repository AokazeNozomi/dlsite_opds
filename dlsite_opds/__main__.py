"""Entry point for ``python -m dlsite_opds``."""

import logging
import sys

import uvicorn

from .core.config import Settings, load_settings


def _configure_logging(cfg: Settings) -> None:
    """Configure logging, honouring ``DLSITE_OPDS_LOG_LEVEL``.

    The configured level applies to the application's own ``dlsite_opds``
    loggers. Noisy third-party loggers are pinned to INFO so enabling
    DEBUG does not flood logs with library internals.
    """
    level = getattr(logging, cfg.log_level, logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s:     %(message)s")
    logging.getLogger("dlsite_opds").setLevel(level)
    if level <= logging.DEBUG:
        for noisy in ("aiohttp", "asyncio", "urllib3", "uvicorn.error"):
            logging.getLogger(noisy).setLevel(logging.INFO)
        logging.getLogger("dlsite_opds").debug(
            "Debug logging enabled (DLSITE_OPDS_LOG_LEVEL=%s)", cfg.log_level
        )


def main() -> None:
    cfg = load_settings()
    _configure_logging(cfg)
    if getattr(sys, "frozen", False):
        from dlsite_opds.app import app

        uvicorn.run(app, host=cfg.host, port=cfg.port, access_log=False)
    else:
        uvicorn.run(
            "dlsite_opds.app:app",
            host=cfg.host,
            port=cfg.port,
            access_log=False,
        )


if __name__ == "__main__":
    main()
