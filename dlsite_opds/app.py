"""FastAPI application — OPDS + OPDS-PSE routes."""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request

from .core.auth import ClientPool, SourceImageLRU
from .core.config import load_settings
from .core.http_utils import background_tasks
from .core.progress import ProgressManager
from .routes import covers, downloads, opds, pages, progress
from .services.image_cache import ImageCache

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    cfg = load_settings()
    _app.state.settings = cfg
    _app.state.pool = ClientPool(cfg.cache_ttl)
    _app.state.progress_manager = ProgressManager(cfg.progress_dir)
    _app.state.image_cache = ImageCache(cfg.image_cache_dir, cfg.image_cache_ttl)
    _app.state.source_cache = SourceImageLRU()
    _app.state.image_executor = ThreadPoolExecutor(
        max_workers=min(4, os.cpu_count() or 2),
        thread_name_prefix="img",
    )
    _app.state.prefetch_inflight: set[tuple[str, str, int, int | None]] = set()
    _app.state.cover_semaphore = asyncio.Semaphore(cfg.cover_concurrency)
    _app.state.cover_session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
    )
    _app.state.cover_cache: dict[str, tuple[bytes, str]] = {}
    yield
    for task in background_tasks:
        task.cancel()
    background_tasks.clear()
    _app.state.image_executor.shutdown(wait=False)
    await _app.state.cover_session.close()
    await _app.state.pool.close_all()


app = FastAPI(title="DLsite OPDS", lifespan=lifespan)

app.include_router(opds.router)
app.include_router(pages.router)
app.include_router(covers.router)
app.include_router(downloads.router)
app.include_router(progress.router)


@app.middleware("http")
async def log_request_time(request: Request, call_next):  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    logger.info(
        "%s %s %d %.0fms",
        request.method,
        path,
        response.status_code,
        elapsed_ms,
    )
    return response
