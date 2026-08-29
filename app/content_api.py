from __future__ import annotations

import asyncio
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .auth import verify_password
from .audio_library import (
    AudioLibraryError,
    _safe_audio_path,
    delete_audio_if_unpublished,
    import_uploaded_audio,
    import_youtube_audio,
)
from .content_loader import ContentError
from .content_store import (
    backup_to_github,
    content_revision,
    content_status,
    load_editable_site,
    save_editable_site,
    trigger_portainer_redeploy,
    validate_site,
)
from .db import db_conn
from .ocr import OcrError, extract_english_hymn_text, ocr_available

router = APIRouter(prefix="/api/content", tags=["content-manager"])

_audio_import_jobs: dict[str, dict[str, Any]] = {}


def _prune_audio_jobs() -> None:
    now = time.monotonic()
    stale = [
        job_id
        for job_id, job in _audio_import_jobs.items()
        if job.get("state") in {"done", "error"} and now - float(job.get("updated", now)) > 3600
    ]
    for job_id in stale:
        _audio_import_jobs.pop(job_id, None)


async def _run_youtube_audio_job(job_id: str, url: str) -> None:
    job = _audio_import_jobs.get(job_id)
    if not job:
        return
    job["state"] = "working"
    job["updated"] = time.monotonic()
    try:
        result = await asyncio.to_thread(import_youtube_audio, url)
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
    else:
        job["state"] = "done"
        job["recording"] = result
    job["updated"] = time.monotonic()



def _api_secret() -> str:
    secret = os.getenv("CONTENT_API_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The content publishing API has not been configured by the site administrator.",
        )
    return secret


def _token_ttl() -> int:
    try:
        return max(300, min(86400, int(os.getenv("CONTENT_API_TOKEN_TTL", "7200"))))
    except ValueError:
        return 7200


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_api_secret(), salt="stminahs-content-manager-v1")


def _bearer_token(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in to the Content Manager first.")
    token = value[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing Content Manager token.")
    return token


def require_content_admin(request: Request) -> dict[str, Any]:
    token = _bearer_token(request)
    try:
        payload = _serializer().loads(token, max_age=_token_ttl())
    except SignatureExpired as exc:
        raise HTTPException(status_code=401, detail="Your Content Manager session expired. Sign in again.") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail="Invalid Content Manager session.") from exc

    user_id = payload.get("uid") if isinstance(payload, dict) else None
    stamp = payload.get("stamp") if isinstance(payload, dict) else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Content Manager session.")

    with db_conn() as db:
        row = db.execute(
            "SELECT id, username, display_name, role, active, updated_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if row is None or not row["active"] or row["role"] != "admin" or row["updated_at"] != stamp:
        raise HTTPException(status_code=401, detail="This Content Manager session is no longer valid.")
    return dict(row)


@router.post("/login")
async def content_login(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Expected a JSON login request.") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON login object.")
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not username or not password:
        raise HTTPException(status_code=400, detail="Enter your administrator username and password.")

    # Force configuration check before credential work so a misconfigured API is obvious.
    _api_secret()

    with db_conn() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()

    if row is None or not row["active"] or row["role"] != "admin" or not verify_password(password, row["password_hash"]):
        await asyncio.sleep(0.45)
        raise HTTPException(status_code=401, detail="The administrator username or password is incorrect.")

    token = _serializer().dumps({"uid": row["id"], "stamp": row["updated_at"]})
    return {
        "ok": True,
        "token": token,
        "expires_in": _token_ttl(),
        "user": {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
        },
    }


@router.get("/current")
async def content_current(request: Request):
    user = require_content_admin(request)
    return {
        "ok": True,
        "content": load_editable_site(),
        "status": content_status(),
        "user": {"username": user["username"], "display_name": user["display_name"]},
    }


@router.post("/validate")
async def content_validate(request: Request):
    require_content_admin(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ContentError("Expected a JSON request object.")
        content = body.get("content")
        if not isinstance(content, dict):
            raise ContentError("The publish request did not contain a content object.")
        warnings = validate_site(content)
        return {"ok": True, "warnings": warnings}
    except ContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/ocr/english")
async def content_ocr_english(
    request: Request,
    image: UploadFile = File(...),
):
    """Run free local English OCR for the Content Manager bulk importer."""
    require_content_admin(request)

    content_type = str(image.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        await image.close()
        raise HTTPException(status_code=400, detail="Choose an image file for OCR.")

    max_bytes = 15 * 1024 * 1024
    data = await image.read(max_bytes + 1)
    filename = str(image.filename or "image.png")
    await image.close()

    if not data:
        raise HTTPException(status_code=400, detail="The selected image was empty.")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="OCR images must be 15 MB or smaller.")

    try:
        result = await asyncio.to_thread(extract_english_hymn_text, data, filename)
        return {"ok": True, **result}
    except OcrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audio/upload")
async def content_audio_upload(
    request: Request,
    audio: UploadFile = File(...),
):
    """Upload an authorized hymn recording and convert it to a managed MP3."""
    require_content_admin(request)

    max_mb_text = os.getenv("HYMN_AUDIO_MAX_MB", os.getenv("MAX_UPLOAD_MB", "40"))
    try:
        max_mb = max(5, min(500, int(max_mb_text)))
    except ValueError:
        max_mb = 40
    max_bytes = max_mb * 1024 * 1024

    data = await audio.read(max_bytes + 1)
    filename = str(audio.filename or "recording")
    await audio.close()
    if not data:
        raise HTTPException(status_code=400, detail="The selected audio file was empty.")
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Hymn audio files must be {max_mb} MB or smaller.",
        )

    try:
        result = await asyncio.to_thread(import_uploaded_audio, data, filename)
        return {"ok": True, "recording": result}
    except AudioLibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audio/import-youtube/start")
async def content_audio_import_youtube_start(request: Request):
    """Start a background YouTube audio import and return immediately."""
    user = require_content_admin(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Expected a JSON request object.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON request object.")
    if body.get("confirm_rights") is not True:
        raise HTTPException(
            status_code=400,
            detail="Confirm that you own this recording or have permission to store and use it.",
        )
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="Enter a YouTube URL.")

    _prune_audio_jobs()
    active_for_user = sum(
        1
        for job in _audio_import_jobs.values()
        if job.get("owner") == user["id"] and job.get("state") in {"queued", "working"}
    )
    if active_for_user >= 2:
        raise HTTPException(status_code=429, detail="Wait for your current YouTube audio import to finish.")

    job_id = secrets.token_urlsafe(18)
    _audio_import_jobs[job_id] = {
        "owner": user["id"],
        "state": "queued",
        "created": time.monotonic(),
        "updated": time.monotonic(),
    }
    asyncio.create_task(_run_youtube_audio_job(job_id, url))
    return {"ok": True, "job_id": job_id, "state": "queued"}


@router.get("/audio/import-youtube/status/{job_id}")
async def content_audio_import_youtube_status(request: Request, job_id: str):
    user = require_content_admin(request)
    _prune_audio_jobs()
    job = _audio_import_jobs.get(job_id)
    if not job or job.get("owner") != user["id"]:
        raise HTTPException(status_code=404, detail="YouTube audio import job not found.")

    state = str(job.get("state", "error"))
    response: dict[str, Any] = {"ok": True, "job_id": job_id, "state": state}
    if state == "done":
        response["recording"] = job.get("recording") or {}
    elif state == "error":
        response["error"] = str(job.get("error") or "YouTube audio import failed.")
    return response


@router.post("/audio/delete-unpublished")
async def content_audio_delete_unpublished(request: Request):
    """Remove a draft-only imported audio file without breaking live content."""
    require_content_admin(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Expected a JSON request object.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON request object.")
    filename = str(body.get("audio_file", "")).strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Missing audio filename.")
    try:
        deleted = await asyncio.to_thread(
            delete_audio_if_unpublished,
            filename,
            load_editable_site(),
        )
        return {"ok": True, "deleted": deleted}
    except AudioLibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audio/file/{filename}")
async def content_audio_file(filename: str):
    """Public same-origin stream for audio referenced by the hymn curriculum."""
    try:
        path = _safe_audio_path(filename)
    except AudioLibraryError as exc:
        raise HTTPException(status_code=404, detail="Audio file not found.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/publish")
async def content_publish(request: Request):
    user = require_content_admin(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ContentError("Expected a JSON request object.")
        content = body.get("content")
        if not isinstance(content, dict):
            raise ContentError("The publish request did not contain a content object.")

        base_revision = str(body.get("base_revision", "")).strip()
        current_revision = content_revision()
        if base_revision and base_revision != current_revision:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The live curriculum changed after this Content Manager loaded it. "
                    "Refresh from the website, re-apply your edit, and publish again."
                ),
            )

        saved, warnings, backup = save_editable_site(content)
        github_result: dict[str, Any] | None = None
        github_error = ""

        if bool(body.get("github_backup")):
            try:
                github_result = backup_to_github(
                    saved,
                    message=f"Content publish by {user['display_name']}",
                )
            except ContentError as exc:
                # Publishing is already complete. Return this as a non-fatal backup warning.
                github_error = str(exc)

        return {
            "ok": True,
            "message": "Website content published successfully.",
            "warnings": warnings,
            "backup_created": backup.name if backup else "",
            "github": github_result,
            "github_error": github_error,
            "status": content_status(),
        }
    except ContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/redeploy")
async def content_redeploy(request: Request):
    require_content_admin(request)
    try:
        result = trigger_portainer_redeploy()
        return {"ok": True, "message": "Portainer redeploy was triggered.", "portainer": result}
    except ContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/status")
async def content_api_status(request: Request):
    user = require_content_admin(request)
    return {
        "ok": True,
        "status": content_status(),
        "ocr": {
            "available": ocr_available(),
            "engine": "tesseract",
        },
        "user": {
            "username": user["username"],
            "display_name": user["display_name"],
        },
    }
