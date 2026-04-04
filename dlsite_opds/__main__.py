"""Entry point for ``python -m dlsite_opds``."""

import logging
import sys

import uvicorn

from .core.config import load_settings


def main() -> None:
    cfg = load_settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
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
