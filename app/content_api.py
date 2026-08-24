from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .auth import verify_password
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
