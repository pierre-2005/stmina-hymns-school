from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def database_path() -> str:
    return os.getenv("DATABASE_PATH", "/app/data/stminahs.db")


@contextmanager
def db_conn() -> Iterator[sqlite3.Connection]:
    path = Path(database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with db_conn() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('student','teacher','admin')),
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                level_slug TEXT NOT NULL DEFAULT '',
                year_slug TEXT NOT NULL DEFAULT '',
                teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS class_students (
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY (class_id, student_id)
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
                hymn_slug TEXT NOT NULL,
                hymn_title TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'submitted'
                    CHECK(status IN ('submitted','reviewed','needs-practice','approved')),
                teacher_feedback TEXT NOT NULL DEFAULT '',
                reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                reviewed_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attendance_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                session_date TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(class_id, session_date)
            );

            CREATE TABLE IF NOT EXISTS attendance_records (
                session_id INTEGER NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('present','absent','late','excused')),
                note TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, student_id)
            );

            CREATE TABLE IF NOT EXISTS developer_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                area TEXT NOT NULL DEFAULT 'General',
                priority TEXT NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('low','normal','high','urgent')),
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','planned','resolved','closed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_submissions_class ON submissions(class_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_attendance_class_date ON attendance_sessions(class_id, session_date DESC);
            CREATE INDEX IF NOT EXISTS idx_comments_created ON developer_comments(created_at DESC);
            """
        )
