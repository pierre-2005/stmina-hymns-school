from __future__ import annotations

import unicodedata
import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    csrf_token,
    get_current_user,
    hash_password,
    require_user,
    seed_initial_admin,
    verify_admin_setup_key,
    verify_csrf,
    verify_password,
)
from .content_api import router as content_api_router
from .content_loader import ContentError, find_hymn, find_level, find_year, flatten_hymns, load_site
from .content_store import ensure_content_json
from .db import db_conn, init_db, utc_now_iso

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "40")) * 1024 * 1024
TIMEZONE = ZoneInfo(os.getenv("TZ", "America/Edmonton"))
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".oga", ".webm", ".aac"}
ROLE_LABELS = {"student": "Student", "teacher": "Teacher", "admin": "Administrator"}
ATTENDANCE_STATUSES = {"present", "absent", "late", "excused"}
SUBMISSION_STATUSES = {"submitted", "reviewed", "needs-practice", "approved"}
COMMENT_STATUSES = {"open", "planned", "resolved", "closed"}
COMMENT_PRIORITIES = {"low", "normal", "high", "urgent"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    seed_initial_admin()
    # v3 uses persistent JSON content in /app/data. On the first v3 startup,
    # the current Excel workbook is migrated automatically so no curriculum is lost.
    ensure_content_json()
    yield


app = FastAPI(title="St. Mina Hymns School", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", secrets.token_urlsafe(48)),
    session_cookie="stminahs_session",
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes", "on"},
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(content_api_router)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

def avva_legacy_text(value: Any) -> str:
    """
    Convert Unicode combining grave accents into the legacy
    accent encoding expected by Avva Shenouda.

    Examples:
        è  ->  `e
        t̀  ->  `t
        ǹ  ->  `n
        à  ->  `a

    This only changes the text sent to the browser.
    The saved hymn content is left untouched.
    """
    text = unicodedata.normalize("NFD", str(value or ""))

    output: list[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        # Base character followed by Unicode combining grave.
        if (
            i + 1 < len(text)
            and text[i + 1] == "\u0300"
        ):
            # Avva Shenouda expects the legacy grave character
            # before the character it belongs to.
            output.append("`")
            output.append(char)
            i += 2
            continue

        output.append(char)
        i += 1

    return "".join(output)


templates.env.filters["avva_legacy"] = avva_legacy_text

def get_site() -> dict[str, Any]:
    # The Content Manager writes this file atomically. load_site automatically
    # invalidates its cache whenever the file mtime/size changes, so published
    # changes appear without restarting or redeploying the container.
    return load_site(str(ensure_content_json()))


def pop_flash(request: Request) -> dict[str, str] | None:
    return request.session.pop("flash", None)


def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def page_context(request: Request, title: str, **values: Any) -> dict[str, Any]:
    site = values.pop("site", None) or get_site()
    user = get_current_user(request)
    return {
        "request": request,
        "site": site,
        "title": title,
        "current_user": user,
        "role_label": ROLE_LABELS.get(user["role"], "") if user else "",
        "csrf_token": csrf_token(request),
        "flash": pop_flash(request),
        **values,
    }


def render(request: Request, template: str, title: str, *, status_code: int = 200, **values: Any):
    return templates.TemplateResponse(
        name=template,
        context=page_context(request, title, **values),
        status_code=status_code,
    )


def redirect(path: str, status_code: int = status.HTTP_303_SEE_OTHER) -> RedirectResponse:
    return RedirectResponse(path, status_code=status_code)


def safe_next_url(value: str | None, fallback: str = "/portal") -> str:
    if not value:
        return fallback
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return fallback
    return value


def role_home(user: dict[str, Any]) -> str:
    return "/portal"


def local_today() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def get_accessible_classes(user: dict[str, Any], *, include_inactive: bool = False) -> list[dict[str, Any]]:
    active_clause = "" if include_inactive else "AND c.active = 1"
    with db_conn() as db:
        if user["role"] == "admin":
            rows = db.execute(
                f"""
                SELECT c.*, u.display_name AS teacher_name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
                FROM classes c
                LEFT JOIN users u ON u.id = c.teacher_id
                WHERE 1=1 {active_clause}
                ORDER BY c.name COLLATE NOCASE
                """
            ).fetchall()
        elif user["role"] == "teacher":
            rows = db.execute(
                f"""
                SELECT c.*, u.display_name AS teacher_name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
                FROM classes c
                LEFT JOIN users u ON u.id = c.teacher_id
                WHERE c.teacher_id = ? {active_clause}
                ORDER BY c.name COLLATE NOCASE
                """,
                (user["id"],),
            ).fetchall()
        else:
            rows = db.execute(
                f"""
                SELECT c.*, u.display_name AS teacher_name
                FROM classes c
                JOIN class_students cs ON cs.class_id = c.id
                LEFT JOIN users u ON u.id = c.teacher_id
                WHERE cs.student_id = ? {active_clause}
                ORDER BY c.name COLLATE NOCASE
                """,
                (user["id"],),
            ).fetchall()
    return [dict(row) for row in rows]


def can_manage_class(user: dict[str, Any], class_id: int) -> bool:
    if user["role"] == "admin":
        return True
    if user["role"] != "teacher":
        return False
    with db_conn() as db:
        return db.execute(
            "SELECT 1 FROM classes WHERE id = ? AND teacher_id = ? AND active = 1",
            (class_id, user["id"]),
        ).fetchone() is not None


def resolve_hymn_ref(site: dict[str, Any], hymn_ref: str) -> dict[str, str] | None:
    parts = hymn_ref.split("::", 2)
    if len(parts) != 3:
        return None
    level = find_level(site, parts[0])
    year = find_year(level, parts[1]) if level else None
    hymn = find_hymn(year, parts[2]) if year else None
    if not level or not year or not hymn:
        return None
    return {
        "level_slug": level["slug"],
        "year_slug": year["slug"],
        "slug": hymn["slug"],
        "title": hymn["title"],
    }


def clean_filename(filename: str) -> str:
    filename = Path(filename or "recording").name
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", filename).strip(" .") or "recording"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    return response


@app.exception_handler(404)
async def not_found(request: Request, _exc):
    return render(request, "404.html", "Page not found", status_code=404)


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    return render(
        request,
        "error.html",
        "Access denied",
        status_code=403,
        heading="Access denied",
        message=getattr(exc, "detail", "You do not have permission to view this page."),
    )


@app.exception_handler(ContentError)
async def content_error(request: Request, exc: ContentError):
    fallback_site = {
        "site_title": "St. Mina Hymns School",
        "site_subtitle": "Content configuration error",
        "footer_text": "St. Mina Coptic Orthodox Church (Calgary)",
        "levels": [],
        "languages": [],
    }
    return render(
        request,
        "error.html",
        "Content error",
        status_code=500,
        site=fallback_site,
        heading="The hymn content could not be loaded",
        message=str(exc),
    )


@app.get("/health")
async def health():
    try:
        site = get_site()
        return {"ok": True, "levels": len(site.get("levels", [])), "warnings": site.get("content_warnings", [])}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    site = get_site()
    return render(request, "index.html", site.get("site_title", "Hymns"), site=site)


@app.get("/levels/{level_slug}", response_class=HTMLResponse)
async def level_page(request: Request, level_slug: str):
    site = get_site()
    level = find_level(site, level_slug)
    if not level:
        raise HTTPException(status_code=404)
    return render(request, "level.html", level["name"], site=site, level=level)


@app.get("/levels/{level_slug}/{year_slug}", response_class=HTMLResponse)
async def year_page(request: Request, level_slug: str, year_slug: str):
    site = get_site()
    level = find_level(site, level_slug)
    year = find_year(level, year_slug) if level else None
    if not level or not year:
        raise HTTPException(status_code=404)
    return render(request, "year.html", year["name"], site=site, level=level, year=year)


@app.get("/levels/{level_slug}/{year_slug}/{hymn_slug}", response_class=HTMLResponse)
async def hymn_page(request: Request, level_slug: str, year_slug: str, hymn_slug: str):
    site = get_site()
    level = find_level(site, level_slug)
    year = find_year(level, year_slug) if level else None
    hymn = find_hymn(year, hymn_slug) if year else None
    if not level or not year or not hymn:
        raise HTTPException(status_code=404)
    return render(
        request,
        "hymn.html",
        hymn["title"],
        site=site,
        level=level,
        year=year,
        hymn=hymn,
        languages=site.get("languages", []),
        segments=hymn.get("segments", []),
        recordings=hymn.get("recordings", []),
    )



@app.get("/setup-admin", response_class=HTMLResponse)
async def setup_admin_page(request: Request):
    """Browser-based administrator recovery. Protected by ADMIN_SETUP_KEY."""
    return render(
        request,
        "setup_admin.html",
        "Administrator setup",
        error="",
    )


@app.post("/setup-admin")
async def setup_admin_submit(
    request: Request,
    setup_key: str = Form(...),
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)

    if not verify_admin_setup_key(setup_key):
        await asyncio.sleep(0.35)
        return render(
            request,
            "setup_admin.html",
            "Administrator setup",
            status_code=400,
            error="The administrator setup key is incorrect or has not been configured in Portainer.",
        )

    username = username.strip()
    display_name = display_name.strip()

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        return render(
            request,
            "setup_admin.html",
            "Administrator setup",
            status_code=400,
            error="Usernames must be 3–40 characters and use letters, numbers, dots, dashes, or underscores.",
        )
    if not display_name:
        return render(
            request,
            "setup_admin.html",
            "Administrator setup",
            status_code=400,
            error="Enter the administrator's display name.",
        )
    if password != confirm_password:
        return render(
            request,
            "setup_admin.html",
            "Administrator setup",
            status_code=400,
            error="The passwords do not match.",
        )
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        return render(
            request,
            "setup_admin.html",
            "Administrator setup",
            status_code=400,
            error=str(exc),
        )

    now = utc_now_iso()
    with db_conn() as db:
        existing = db.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if existing:
            user_id = existing["id"]
            db.execute(
                """
                UPDATE users
                SET display_name = ?, role = 'admin', password_hash = ?, active = 1, updated_at = ?
                WHERE id = ?
                """,
                (display_name[:120], password_hash, now, user_id),
            )
        else:
            cursor = db.execute(
                """
                INSERT INTO users(username, display_name, role, password_hash, active, created_at, updated_at)
                VALUES (?, ?, 'admin', ?, 1, ?, ?)
                """,
                (username, display_name[:120], password_hash, now, now),
            )
            user_id = cursor.lastrowid

    request.session.clear()
    request.session["user_id"] = user_id
    csrf_token(request)
    flash(request, "Administrator access is ready. You can now create teacher and student accounts.")
    return redirect("/admin/users")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    if get_current_user(request):
        return redirect("/portal")
    return render(request, "login.html", "Sign in", next_url=safe_next_url(next, "/portal"), error="")


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/portal"),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    with db_conn() as db:
        row = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()
    if row is None or not row["active"] or not verify_password(password, row["password_hash"]):
        await asyncio.sleep(0.35)
        return render(
            request,
            "login.html",
            "Sign in",
            status_code=400,
            next_url=safe_next_url(next_url, "/portal"),
            error="The username or password is incorrect.",
        )
    request.session.clear()
    request.session["user_id"] = row["id"]
    csrf_token(request)
    flash(request, f"Welcome back, {row['display_name']}.")
    return redirect(safe_next_url(next_url, role_home(dict(row))))


@app.post("/logout")
async def logout(request: Request, csrf: str = Form(...)):
    verify_csrf(request, csrf)
    request.session.clear()
    return redirect("/")


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    user = require_user(request, "student", "teacher", "admin")
    return render(request, "account.html", "My account", user=user, error="")


@app.post("/account/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    user = require_user(request, "student", "teacher", "admin")
    with db_conn() as db:
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            return render(request, "account.html", "My account", user=user, error="Current password is incorrect.")
        if new_password != confirm_password:
            return render(request, "account.html", "My account", user=user, error="The new passwords do not match.")
        try:
            new_hash = hash_password(new_password)
        except ValueError as exc:
            return render(request, "account.html", "My account", user=user, error=str(exc))
        db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (new_hash, utc_now_iso(), user["id"]))
    flash(request, "Your password was updated.")
    return redirect("/account")


@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request):
    user = require_user(request, "student", "teacher", "admin")
    classes = get_accessible_classes(user)
    stats: dict[str, int] = {}
    with db_conn() as db:
        if user["role"] == "student":
            stats["submissions"] = db.execute(
                "SELECT COUNT(*) AS n FROM submissions WHERE student_id = ?", (user["id"],)
            ).fetchone()["n"]
        else:
            class_ids = [item["id"] for item in classes]
            if class_ids:
                placeholders = ",".join("?" for _ in class_ids)
                stats["submissions"] = db.execute(
                    f"SELECT COUNT(*) AS n FROM submissions WHERE class_id IN ({placeholders})", class_ids
                ).fetchone()["n"]
            else:
                stats["submissions"] = 0
            stats["comments"] = db.execute(
                "SELECT COUNT(*) AS n FROM developer_comments WHERE user_id = ?", (user["id"],)
            ).fetchone()["n"]
    return render(request, "portal.html", "Portal", user=user, classes=classes, stats=stats)


@app.get("/student/submissions", response_class=HTMLResponse)
async def student_submissions(request: Request):
    user = require_user(request, "student")
    site = get_site()
    classes = get_accessible_classes(user)
    with db_conn() as db:
        rows = db.execute(
            """
            SELECT s.*, c.name AS class_name
            FROM submissions s
            LEFT JOIN classes c ON c.id = s.class_id
            WHERE s.student_id = ?
            ORDER BY s.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    return render(
        request,
        "student_submissions.html",
        "My recordings",
        site=site,
        classes=classes,
        hymn_options=flatten_hymns(site),
        submissions=[dict(row) for row in rows],
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
    )


@app.post("/student/submissions")
async def student_submission_upload(
    request: Request,
    class_id: int = Form(...),
    hymn_ref: str = Form(...),
    notes: str = Form(""),
    recording: UploadFile = File(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    user = require_user(request, "student")
    with db_conn() as db:
        membership = db.execute(
            """
            SELECT c.id FROM classes c
            JOIN class_students cs ON cs.class_id = c.id
            WHERE c.id = ? AND cs.student_id = ? AND c.active = 1
            """,
            (class_id, user["id"]),
        ).fetchone()
    if membership is None:
        raise HTTPException(status_code=403, detail="You are not enrolled in that class.")

    hymn = resolve_hymn_ref(get_site(), hymn_ref)
    if hymn is None:
        flash(request, "Please choose a valid hymn.", "error")
        return redirect("/student/submissions")

    original_name = clean_filename(recording.filename or "recording")
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        flash(request, "Use an MP3, M4A, WAV, OGG, WEBM, or AAC audio file.", "error")
        return redirect("/student/submissions")

    stored_name = f"{uuid4().hex}{extension}"
    destination = UPLOAD_DIR / stored_name
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await recording.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError(f"The recording is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                output.write(chunk)
        if size == 0:
            raise ValueError("The uploaded recording is empty.")
    except Exception as exc:
        destination.unlink(missing_ok=True)
        flash(request, str(exc), "error")
        return redirect("/student/submissions")
    finally:
        await recording.close()

    content_type = recording.content_type or "application/octet-stream"
    with db_conn() as db:
        db.execute(
            """
            INSERT INTO submissions(
                student_id, class_id, hymn_slug, hymn_title, original_filename,
                stored_filename, content_type, size_bytes, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                class_id,
                hymn["slug"],
                hymn["title"],
                original_name,
                stored_name,
                content_type,
                size,
                notes.strip()[:2000],
                utc_now_iso(),
            ),
        )
    flash(request, "Your recording was uploaded privately for your teacher.")
    return redirect("/student/submissions")


@app.post("/student/submissions/{submission_id}/delete")
async def student_submission_delete(request: Request, submission_id: int, csrf: str = Form(...)):
    verify_csrf(request, csrf)
    user = require_user(request, "student")
    with db_conn() as db:
        row = db.execute(
            "SELECT * FROM submissions WHERE id = ? AND student_id = ?", (submission_id, user["id"])
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        db.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
    (UPLOAD_DIR / row["stored_filename"]).unlink(missing_ok=True)
    flash(request, "The recording was deleted.")
    return redirect("/student/submissions")


@app.get("/submissions/{submission_id}/audio")
async def submission_audio(request: Request, submission_id: int):
    user = require_user(request, "student", "teacher", "admin")
    with db_conn() as db:
        row = db.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404)
    if user["role"] == "student" and row["student_id"] != user["id"]:
        raise HTTPException(status_code=403)
    if user["role"] == "teacher" and (row["class_id"] is None or not can_manage_class(user, row["class_id"])):
        raise HTTPException(status_code=403)
    path = (UPLOAD_DIR / row["stored_filename"]).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.exists():
        raise HTTPException(status_code=404, detail="The audio file is missing.")
    return FileResponse(
        path,
        media_type=row["content_type"],
        filename=row["original_filename"],
        content_disposition_type="inline",
    )


@app.get("/teacher/submissions", response_class=HTMLResponse)
async def teacher_submissions(request: Request, class_id: int | None = None):
    user = require_user(request, "teacher", "admin")
    classes = get_accessible_classes(user)
    allowed_ids = {item["id"] for item in classes}
    if class_id and class_id not in allowed_ids:
        raise HTTPException(status_code=403)

    with db_conn() as db:
        params: list[Any] = []
        conditions: list[str] = []
        if user["role"] == "teacher":
            if not allowed_ids:
                rows = []
                return render(request, "teacher_submissions.html", "Student recordings", classes=classes, submissions=rows, selected_class=class_id)
            placeholders = ",".join("?" for _ in allowed_ids)
            conditions.append(f"s.class_id IN ({placeholders})")
            params.extend(sorted(allowed_ids))
        if class_id:
            conditions.append("s.class_id = ?")
            params.append(class_id)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = db.execute(
            f"""
            SELECT s.*, u.display_name AS student_name, u.username,
                   c.name AS class_name, reviewer.display_name AS reviewer_name
            FROM submissions s
            JOIN users u ON u.id = s.student_id
            LEFT JOIN classes c ON c.id = s.class_id
            LEFT JOIN users reviewer ON reviewer.id = s.reviewed_by
            {where}
            ORDER BY s.created_at DESC
            """,
            params,
        ).fetchall()
    return render(
        request,
        "teacher_submissions.html",
        "Student recordings",
        classes=classes,
        submissions=[dict(row) for row in rows],
        selected_class=class_id,
    )


@app.post("/teacher/submissions/{submission_id}/review")
async def review_submission(
    request: Request,
    submission_id: int,
    review_status: str = Form(...),
    feedback: str = Form(""),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    user = require_user(request, "teacher", "admin")
    if review_status not in SUBMISSION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid review status.")
    with db_conn() as db:
        row = db.execute("SELECT class_id FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        if row["class_id"] is None or not can_manage_class(user, row["class_id"]):
            raise HTTPException(status_code=403)
        db.execute(
            """
            UPDATE submissions
            SET status = ?, teacher_feedback = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (review_status, feedback.strip()[:4000], user["id"], utc_now_iso(), submission_id),
        )
    flash(request, "The review was saved.")
    return redirect("/teacher/submissions")


@app.get("/teacher/attendance", response_class=HTMLResponse)
async def attendance_home(request: Request):
    user = require_user(request, "teacher", "admin")
    return render(request, "attendance_home.html", "Attendance", classes=get_accessible_classes(user))


@app.get("/teacher/attendance/{class_id}", response_class=HTMLResponse)
async def attendance_class(request: Request, class_id: int, session_date: str | None = None):
    user = require_user(request, "teacher", "admin")
    if not can_manage_class(user, class_id):
        raise HTTPException(status_code=403)
    selected_date = session_date or local_today()
    try:
        date.fromisoformat(selected_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date.") from exc

    with db_conn() as db:
        class_row = db.execute(
            "SELECT c.*, u.display_name AS teacher_name FROM classes c LEFT JOIN users u ON u.id = c.teacher_id WHERE c.id = ?",
            (class_id,),
        ).fetchone()
        if class_row is None:
            raise HTTPException(status_code=404)
        students = db.execute(
            """
            SELECT u.id, u.display_name, u.username
            FROM users u
            JOIN class_students cs ON cs.student_id = u.id
            WHERE cs.class_id = ? AND u.active = 1
            ORDER BY u.display_name COLLATE NOCASE
            """,
            (class_id,),
        ).fetchall()
        session = db.execute(
            "SELECT * FROM attendance_sessions WHERE class_id = ? AND session_date = ?",
            (class_id, selected_date),
        ).fetchone()
        records: dict[int, dict[str, Any]] = {}
        if session:
            records = {
                row["student_id"]: dict(row)
                for row in db.execute("SELECT * FROM attendance_records WHERE session_id = ?", (session["id"],)).fetchall()
            }
        history = db.execute(
            """
            SELECT a.session_date,
                   SUM(CASE WHEN ar.status = 'present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN ar.status = 'absent' THEN 1 ELSE 0 END) AS absent_count,
                   COUNT(ar.student_id) AS marked_count
            FROM attendance_sessions a
            LEFT JOIN attendance_records ar ON ar.session_id = a.id
            WHERE a.class_id = ?
            GROUP BY a.id
            ORDER BY a.session_date DESC
            LIMIT 12
            """,
            (class_id,),
        ).fetchall()
    return render(
        request,
        "attendance_class.html",
        f"Attendance • {class_row['name']}",
        class_item=dict(class_row),
        students=[dict(row) for row in students],
        records=records,
        selected_date=selected_date,
        session_notes=session["notes"] if session else "",
        history=[dict(row) for row in history],
    )


@app.post("/teacher/attendance/{class_id}")
async def attendance_save(request: Request, class_id: int):
    user = require_user(request, "teacher", "admin")
    if not can_manage_class(user, class_id):
        raise HTTPException(status_code=403)
    form = await request.form()
    verify_csrf(request, str(form.get("csrf", "")))
    selected_date = str(form.get("session_date", ""))
    try:
        date.fromisoformat(selected_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date.") from exc

    now = utc_now_iso()
    with db_conn() as db:
        class_exists = db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone()
        if class_exists is None:
            raise HTTPException(status_code=404)
        db.execute(
            """
            INSERT INTO attendance_sessions(class_id, session_date, notes, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(class_id, session_date)
            DO UPDATE SET notes = excluded.notes, updated_at = excluded.updated_at
            """,
            (class_id, selected_date, str(form.get("session_notes", ""))[:2000], user["id"], now, now),
        )
        session_id = db.execute(
            "SELECT id FROM attendance_sessions WHERE class_id = ? AND session_date = ?",
            (class_id, selected_date),
        ).fetchone()["id"]
        students = db.execute("SELECT student_id FROM class_students WHERE class_id = ?", (class_id,)).fetchall()
        for student in students:
            student_id = student["student_id"]
            attendance_status = str(form.get(f"status_{student_id}", "present"))
            if attendance_status not in ATTENDANCE_STATUSES:
                attendance_status = "present"
            note = str(form.get(f"note_{student_id}", ""))[:500]
            db.execute(
                """
                INSERT INTO attendance_records(session_id, student_id, status, note, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, student_id)
                DO UPDATE SET status = excluded.status, note = excluded.note, updated_at = excluded.updated_at
                """,
                (session_id, student_id, attendance_status, note, now),
            )
    flash(request, f"Attendance for {selected_date} was saved.")
    return redirect(f"/teacher/attendance/{class_id}?session_date={selected_date}")


@app.get("/developer-comments", response_class=HTMLResponse)
async def developer_comments(request: Request):
    user = require_user(request, "teacher", "admin")
    with db_conn() as db:
        if user["role"] == "admin":
            rows = db.execute(
                """
                SELECT dc.*, u.display_name, u.role
                FROM developer_comments dc
                JOIN users u ON u.id = dc.user_id
                ORDER BY dc.created_at DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT dc.*, u.display_name, u.role
                FROM developer_comments dc
                JOIN users u ON u.id = dc.user_id
                WHERE dc.user_id = ?
                ORDER BY dc.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
    return render(request, "developer_comments.html", "Developer comments", comments=[dict(row) for row in rows])


@app.post("/developer-comments")
async def developer_comment_create(
    request: Request,
    title: str = Form(...),
    area: str = Form("General"),
    priority: str = Form("normal"),
    message: str = Form(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    user = require_user(request, "teacher", "admin")
    if priority not in COMMENT_PRIORITIES:
        priority = "normal"
    now = utc_now_iso()
    with db_conn() as db:
        db.execute(
            """
            INSERT INTO developer_comments(user_id, title, area, priority, message, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (user["id"], title.strip()[:160], area.strip()[:80], priority, message.strip()[:8000], now, now),
        )
    flash(request, "Your developer comment was submitted.")
    return redirect("/developer-comments")


@app.post("/admin/comments/{comment_id}/status")
async def developer_comment_status(
    request: Request,
    comment_id: int,
    comment_status: str = Form(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    require_user(request, "admin")
    if comment_status not in COMMENT_STATUSES:
        raise HTTPException(status_code=400)
    with db_conn() as db:
        db.execute(
            "UPDATE developer_comments SET status = ?, updated_at = ? WHERE id = ?",
            (comment_status, utc_now_iso(), comment_id),
        )
    flash(request, "Comment status updated.")
    return redirect("/developer-comments")


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    require_user(request, "admin")

    with db_conn() as db:
        rows = db.execute(
            """
            SELECT u.*,
                   (
                       SELECT COUNT(*)
                       FROM class_students cs
                       WHERE cs.student_id = u.id
                   ) AS class_count,
                   (
                       SELECT COUNT(*)
                       FROM classes c
                       WHERE c.teacher_id = u.id
                   ) AS teaching_count,
                   (
                       SELECT COUNT(*)
                       FROM submissions s
                       WHERE s.student_id = u.id
                   ) AS submission_count,
                   (
                       SELECT COUNT(*)
                       FROM attendance_records ar
                       WHERE ar.student_id = u.id
                   ) AS attendance_count,
                   (
                       SELECT COUNT(*)
                       FROM developer_comments dc
                       WHERE dc.user_id = u.id
                   ) AS comment_count
            FROM users u
            ORDER BY
                CASE u.role
                    WHEN 'admin' THEN 0
                    WHEN 'teacher' THEN 1
                    ELSE 2
                END,
                u.display_name COLLATE NOCASE
            """
        ).fetchall()

    return render(
        request,
        "admin_users.html",
        "Manage users",
        users=[dict(row) for row in rows],
        error="",
    )

@app.post("/admin/users")
async def admin_user_create(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    require_user(request, "admin")
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        flash(request, "Usernames must be 3–40 characters and use letters, numbers, dots, dashes, or underscores.", "error")
        return redirect("/admin/users")
    if role not in ROLE_LABELS:
        flash(request, "Choose a valid role.", "error")
        return redirect("/admin/users")
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect("/admin/users")
    now = utc_now_iso()
    try:
        with db_conn() as db:
            db.execute(
                """
                INSERT INTO users(username, display_name, role, password_hash, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (username, display_name.strip()[:120], role, password_hash, now, now),
            )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            flash(request, "That username is already in use.", "error")
            return redirect("/admin/users")
        raise
    flash(request, f"{display_name.strip()} was added.")
    return redirect("/admin/users")


@app.post("/admin/users/{user_id}/toggle")
async def admin_user_toggle(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    current = require_user(request, "admin")

    if user_id == current["id"]:
        flash(request, "You cannot deactivate the account you are currently using.", "error")
        return redirect("/admin/users")

    with db_conn() as db:
        row = db.execute(
            "SELECT id, display_name, role, active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404)

        new_active = 0 if row["active"] else 1
        if row["role"] == "admin" and not new_active:
            other_active_admins = db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND id <> ?",
                (user_id,),
            ).fetchone()["n"]
            if other_active_admins == 0:
                flash(request, "You cannot deactivate the last active administrator account.", "error")
                return redirect("/admin/users")

        db.execute(
            "UPDATE users SET active = ?, updated_at = ? WHERE id = ?",
            (new_active, utc_now_iso(), user_id),
        )

    flash(request, f"{row['display_name']} was {'activated' if new_active else 'deactivated'}.")
    return redirect("/admin/users")


@app.post("/admin/users/{user_id}/delete")
async def admin_user_delete(
    request: Request,
    user_id: int,
    confirm_username: str = Form(...),
    csrf: str = Form(...),
):
    """Permanently delete an account and its user-owned records/uploads."""

    verify_csrf(request, csrf)
    current = require_user(request, "admin")

    # Prevent the signed-in admin from deleting their own account.
    if user_id == current["id"]:
        flash(
            request,
            "You cannot permanently delete the account you are currently using.",
            "error",
        )
        return redirect("/admin/users")

    stored_filenames: list[str] = []
    deleted_name = ""
    deleted_username = ""

    with db_conn() as db:
        user = db.execute(
            """
            SELECT id, username, display_name, role
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user is None:
            raise HTTPException(status_code=404)

        deleted_name = user["display_name"]
        deleted_username = user["username"]

        # Require the administrator to type the username as confirmation.
        if confirm_username.strip().casefold() != deleted_username.casefold():
            flash(
                request,
                f"Deletion cancelled. Type @{deleted_username} exactly to confirm.",
                "error",
            )
            return redirect("/admin/users")

        # Never allow deletion of the final active administrator.
        if user["role"] == "admin":
            other_active_admins = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM users
                WHERE role = 'admin'
                  AND active = 1
                  AND id <> ?
                """,
                (user_id,),
            ).fetchone()["n"]

            if other_active_admins == 0:
                flash(
                    request,
                    "You cannot delete the last active administrator account.",
                    "error",
                )
                return redirect("/admin/users")

        # Save filenames before deleting the database records.
        stored_filenames = [
            row["stored_filename"]
            for row in db.execute(
                """
                SELECT stored_filename
                FROM submissions
                WHERE student_id = ?
                """,
                (user_id,),
            ).fetchall()
        ]

        # Existing foreign keys automatically remove:
        # - student class enrolments
        # - attendance records
        # - submissions
        # - developer comments
        #
        # Classes taught by a deleted teacher remain, but teacher_id becomes NULL.
        db.execute(
            "DELETE FROM users WHERE id = ?",
            (user_id,),
        )

    # Remove the student's physical audio files.
    removed_files = 0
    upload_root = UPLOAD_DIR.resolve()

    for stored_filename in stored_filenames:
        path = (UPLOAD_DIR / stored_filename).resolve()

        # Prevent unsafe paths from escaping the uploads directory.
        if path.parent != upload_root:
            continue

        try:
            existed = path.exists()
            path.unlink(missing_ok=True)

            if existed:
                removed_files += 1

        except OSError as exc:
            print(
                f"[stminahs] Could not remove deleted user's upload "
                f"{path}: {exc}"
            )

    file_message = ""

    if removed_files:
        word = "recording" if removed_files == 1 else "recordings"
        file_message = f" and removed {removed_files} uploaded {word}"

    flash(
        request,
        f"{deleted_name} (@{deleted_username}) was permanently deleted"
        f"{file_message}.",
    )

    return redirect("/admin/users")

@app.post("/admin/users/{user_id}/password")
async def admin_user_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    require_user(request, "admin")
    try:
        password_hash = hash_password(new_password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect("/admin/users")
    with db_conn() as db:
        result = db.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, utc_now_iso(), user_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404)
    flash(request, "The password was reset.")
    return redirect("/admin/users")


@app.get("/admin/classes", response_class=HTMLResponse)
async def admin_classes(request: Request):
    require_user(request, "admin")
    site = get_site()
    with db_conn() as db:
        teachers = db.execute(
            "SELECT id, display_name FROM users WHERE role IN ('teacher','admin') AND active = 1 ORDER BY display_name"
        ).fetchall()
    return render(
        request,
        "admin_classes.html",
        "Manage classes",
        site=site,
        classes=get_accessible_classes({"role": "admin", "id": 0}, include_inactive=True),
        teachers=[dict(row) for row in teachers],
    )


@app.post("/admin/classes")
async def admin_class_create(
    request: Request,
    name: str = Form(...),
    level_slug: str = Form(""),
    year_slug: str = Form(""),
    teacher_id: str = Form(""),
    csrf: str = Form(...),
):
    verify_csrf(request, csrf)
    require_user(request, "admin")
    now = utc_now_iso()
    teacher_value = int(teacher_id) if teacher_id.isdigit() else None
    with db_conn() as db:
        db.execute(
            """
            INSERT INTO classes(name, level_slug, year_slug, teacher_id, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (name.strip()[:120], level_slug.strip(), year_slug.strip(), teacher_value, now, now),
        )
    flash(request, f"Class '{name.strip()}' was created.")
    return redirect("/admin/classes")


@app.get("/admin/classes/{class_id}", response_class=HTMLResponse)
async def admin_class_detail(request: Request, class_id: int):
    require_user(request, "admin")
    with db_conn() as db:
        class_row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if class_row is None:
            raise HTTPException(status_code=404)
        teachers = db.execute(
            "SELECT id, display_name FROM users WHERE role IN ('teacher','admin') AND active = 1 ORDER BY display_name"
        ).fetchall()
        students = db.execute(
            """
            SELECT u.id, u.display_name, u.username,
                   CASE WHEN cs.class_id IS NULL THEN 0 ELSE 1 END AS enrolled
            FROM users u
            LEFT JOIN class_students cs ON cs.student_id = u.id AND cs.class_id = ?
            WHERE u.role = 'student' AND u.active = 1
            ORDER BY u.display_name COLLATE NOCASE
            """,
            (class_id,),
        ).fetchall()
    return render(
        request,
        "admin_class_detail.html",
        f"Manage {class_row['name']}",
        class_item=dict(class_row),
        teachers=[dict(row) for row in teachers],
        students=[dict(row) for row in students],
    )


@app.post("/admin/classes/{class_id}")
async def admin_class_update(request: Request, class_id: int):
    require_user(request, "admin")
    form = await request.form()
    verify_csrf(request, str(form.get("csrf", "")))
    teacher_text = str(form.get("teacher_id", ""))
    teacher_id = int(teacher_text) if teacher_text.isdigit() else None
    student_ids = {int(value) for value in form.getlist("student_ids") if str(value).isdigit()}
    with db_conn() as db:
        existing = db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404)
        db.execute(
            """
            UPDATE classes
            SET name = ?, level_slug = ?, year_slug = ?, teacher_id = ?, active = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(form.get("name", "")).strip()[:120],
                str(form.get("level_slug", "")).strip(),
                str(form.get("year_slug", "")).strip(),
                teacher_id,
                1 if form.get("active") else 0,
                utc_now_iso(),
                class_id,
            ),
        )
        db.execute("DELETE FROM class_students WHERE class_id = ?", (class_id,))
        valid_students = db.execute(
            "SELECT id FROM users WHERE role = 'student' AND active = 1"
        ).fetchall()
        valid_ids = {row["id"] for row in valid_students}
        db.executemany(
            "INSERT INTO class_students(class_id, student_id) VALUES (?, ?)",
            [(class_id, student_id) for student_id in sorted(student_ids & valid_ids)],
        )
    flash(request, "Class assignments were updated.")
    return redirect(f"/admin/classes/{class_id}")
