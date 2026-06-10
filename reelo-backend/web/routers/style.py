"""Style inference (Module 1: reelo-scriptwriting).

``POST /style/infer`` (multipart) → ``{palette, description}`` from uploaded
reference images. v1 is a heuristic (stdlib colour extraction); see
:func:`module1.style.infer_style`.
"""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from module1.style import infer_style
from web.deps import CurrentUser
from web.schemas import InferStyleResponse

router = APIRouter(prefix="/style", tags=["style"])


@router.post("/infer", response_model=InferStyleResponse)
async def infer_style_endpoint(
    user_id: CurrentUser,
    reference_images: list[UploadFile] = File(default_factory=list),
) -> InferStyleResponse:
    """Infer ``{palette, description}`` from uploaded reference images (multipart)."""
    blobs = [await img.read() for img in reference_images]
    result = infer_style(blobs)
    return InferStyleResponse(palette=result["palette"], description=result["description"])


__all__ = ["router"]
