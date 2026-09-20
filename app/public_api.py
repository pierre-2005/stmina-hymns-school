from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .audio_library import AudioLibraryError, _safe_audio_path
from .content_loader import load_site
from .content_store import ensure_content_json
from .coptic_render import render_coptic

router = APIRouter(prefix="/api/public", tags=["public-site"])


def _published_site_payload() -> dict[str, Any]:
    """Return public curriculum data safe for the static cPanel frontend.

    load_site() already filters unpublished levels/years/hymns/segments and adds
    runtime timing fields.  This copy rewrites managed audio URLs to the public
    read-only endpoint and pre-renders Coptic HTML so the cPanel frontend does
    not need to duplicate the Avva Shenouda conversion logic in JavaScript.
    """
    site = deepcopy(load_site(str(ensure_content_json())))

    # Internal warnings are useful to administrators on the Pi-hosted site, but
    # the public cPanel site does not need to expose them.
    site.pop("content_warnings", None)

    for level in site.get("levels", []) or []:
        for year in level.get("years", []) or []:
            for hymn in year.get("hymns", []) or []:
                for recording in hymn.get("recordings", []) or []:
                    if not isinstance(recording, dict):
                        continue
                    if str(recording.get("type", "soundcloud") or "soundcloud").lower() == "audio":
                        filename = str(recording.get("audio_file", "")).strip()
                        if filename:
                            recording["audio_url"] = (
                                "/api/public/audio/" + quote(filename, safe="")
                            )

                for segment in hymn.get("segments", []) or []:
                    texts = segment.get("texts") or {}
                    if not isinstance(texts, dict):
                        continue
                    coptic = str(texts.get("cop", "") or "")
                    if coptic:
                        segment.setdefault("rendered_html", {})["cop"] = str(
                            render_coptic(coptic)
                        )

    return site


@router.get("/site")
async def public_site():
    return JSONResponse(
        _published_site_payload(),
        headers={
            # Publishing should become visible immediately on the cPanel site.
            "Cache-Control": "no-store, max-age=0",
        },
    )


@router.get("/audio/{filename}")
async def public_audio(filename: str):
    try:
        path = _safe_audio_path(filename)
    except AudioLibraryError as exc:
        raise HTTPException(status_code=404, detail="Audio file not found.") from exc

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")

    return FileResponse(
        Path(path),
        media_type="audio/mpeg",
        filename=path.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "public, max-age=86400",
        },
    )
