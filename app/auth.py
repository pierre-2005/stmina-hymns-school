from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any

from fastapi import HTTPException, Request, status

from .db import db_conn, utc_now_iso

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored_hash.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def seed_initial_admin() -> None:
    with db_conn() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if count:
            return

        username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
        display_name = os.getenv("ADMIN_DISPLAY_NAME", "Site Administrator").strip() or "Site Administrator"
        password = os.getenv("ADMIN_PASSWORD", "").strip()
        generated = False
        if not password:
            password = secrets.token_urlsafe(14)
            generated = True

        now = utc_now_iso()
        db.execute(
            """
            INSERT INTO users(username, display_name, role, password_hash, active, created_at, updated_at)
            VALUES (?, ?, 'admin', ?, 1, ?, ?)
            """,
            (username, display_name, hash_password(password), now, now),
        )

    print("\n[stminahs] Initial administrator created")
    print(f"[stminahs] Username: {username}")
    if generated:
        print(f"[stminahs] Temporary password: {password}")
        print("[stminahs] Change this password immediately after signing in.\n")


def get_current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with db_conn() as db:
        row = db.execute(
            "SELECT id, username, display_name, role, active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if row is None or not row["active"]:
        request.session.clear()
        return None
    return dict(row)


def require_user(request: Request, *roles: str) -> dict[str, Any]:
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": f"/login?next={request.url.path}"})
    if roles and user["role"] not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to view this page.")
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted_token: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(str(expected), str(submitted_token or "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The form expired. Please reload and try again.")
