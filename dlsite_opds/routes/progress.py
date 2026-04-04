"""Reading progress routes."""

from fastapi import APIRouter, Depends, HTTPException
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
) -> dict[str, object]:
    """Store reading progress (``last_read`` is 1-based page number)."""
    auth.progress.set(product_id, body.last_read)
    return {"ok": True, "product_id": product_id, "last_read": body.last_read}


@router.get("/progress/{product_id}")
async def get_progress(
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> dict[str, object]:
    prog = auth.progress.get(product_id)
    if prog is None:
        raise HTTPException(status_code=404, detail="No progress recorded")
    return {"product_id": product_id, **prog}
