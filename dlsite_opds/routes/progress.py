"""Reading progress routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..core.auth import AuthContext, get_auth

router = APIRouter()


class ProgressBody(BaseModel):
    last_read: int


@router.put("/progress/{product_id}")
async def update_progress(
    product_id: str,
    body: ProgressBody,
    auth: AuthContext = Depends(get_auth),
    chapter: str | None = Query(None),
) -> dict[str, object]:
    """Store reading progress (``last_read`` is 1-based page number)."""
    auth.progress.set(product_id, body.last_read, chapter=chapter)
    result: dict[str, object] = {
        "ok": True,
        "product_id": product_id,
        "last_read": body.last_read,
    }
    if chapter is not None:
        result["chapter"] = chapter
    return result


@router.get("/progress/{product_id}")
async def get_progress(
    product_id: str,
    auth: AuthContext = Depends(get_auth),
    chapter: str | None = Query(None),
) -> dict[str, object]:
    prog = auth.progress.get(product_id, chapter=chapter)
    if prog is None:
        raise HTTPException(status_code=404, detail="No progress recorded")
    result: dict[str, object] = {"product_id": product_id, **prog}
    if chapter is not None:
        result["chapter"] = chapter
    return result
