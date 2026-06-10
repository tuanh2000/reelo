"""Publish / export (Module 2: reelo-video-generator).

v1 exports signed URLs + metadata only (the user uploads to YouTube themselves —
Module 2 §12). Reads the episode's asset keys (filled by the runner) and the
chosen thumbnail (``PublishMeta.thumbnailIndex``), and returns signed URLs.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from db.repository import EpisodeRepo
from module1.persistence import find_series_for_episode
from storage import get_storage
from web.deps import CurrentUser, DbSession
from web.schemas import PublishExportRequest, PublishExportResponse

router = APIRouter(prefix="/publish", tags=["publish"])


@router.post("/export", response_model=PublishExportResponse)
async def publish_export(
    body: PublishExportRequest, user_id: CurrentUser, db: DbSession
) -> PublishExportResponse:
    """Return signed video/srt/thumbnail URLs + metadata (maps ``publishToYouTube``).

    Requires the episode to be assembled (``final.mp4`` present). The thumbnail is
    chosen by ``meta.thumbnailIndex`` (0-based into the uploaded thumbnail keys).
    Metadata merges the episode's ``youtube`` block with the Review-screen meta.
    """
    ep_row = await EpisodeRepo(db).get(user_id, body.episode_id)
    if ep_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"episode {body.episode_id} not found",
        )
    paths = ep_row.paths or {}
    final_key = paths.get("final")
    if not final_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="episode not assembled yet (no final.mp4)",
        )

    storage = get_storage()
    video_url = await storage.signed_url(final_key)

    srt_url = None
    if paths.get("srt"):
        srt_url = await storage.signed_url(paths["srt"])

    thumbnail_url = None
    thumbs = [t for t in (paths.get("thumbnails") or "").split(",") if t]
    if thumbs:
        idx = body.meta.thumbnail_index
        if idx < 0 or idx >= len(thumbs):
            idx = 0
        thumbnail_url = await storage.signed_url(thumbs[idx])

    # Metadata: episode.youtube (if scripted) overlaid with the Review-screen meta.
    metadata: dict = {}
    found = await find_series_for_episode(db, user_id, body.episode_id)
    if found is not None:
        _, _, ep = found
        if ep.youtube:
            metadata.update(ep.youtube)
    metadata.update(
        {
            "title": body.meta.title,
            "description": body.meta.description,
            "tags": body.meta.tags,
            "visibility": body.meta.visibility,
        }
    )

    return PublishExportResponse(
        video_url=video_url,
        srt_url=srt_url,
        thumbnail_url=thumbnail_url,
        metadata=metadata,
    )


__all__ = ["router"]
