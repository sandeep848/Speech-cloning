"""Database connection and initialization for Voice Comparison Studio."""

from __future__ import annotations
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import artifact_storage
import schema
import security


def get_db_path(root: Path) -> Path:
    return root / "studio.sqlite3"


@contextmanager
def db_connection(db_path: Path):
    connection = sqlite3.connect(db_path, timeout=5)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database(db_path: Path) -> None:
    with db_connection(db_path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        schema.initialize(c)
        security.initialize(c)
        if not schema.missing_constraints(c):
            c.execute(
                "INSERT OR IGNORE INTO schema_migrations VALUES (2,?)", (time.time(),)
            )
        artifact_storage.initialize(c)
        c.execute(
            "CREATE TABLE IF NOT EXISTS reference_versions ("
            "reference_id TEXT PRIMARY KEY REFERENCES references_audio(id), "
            "transcript TEXT NOT NULL, language TEXT NOT NULL)"
        )
        c.execute(
            "INSERT OR IGNORE INTO reference_versions "
            "SELECT reference_id,transcript,language FROM user_reference_profiles"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS job_observations ("
            "job_id TEXT PRIMARY KEY REFERENCES jobs(id), "
            "first_audio_ms REAL, language TEXT NOT NULL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS job_provenance ("
            "job_id TEXT PRIMARY KEY REFERENCES jobs(id), "
            "configuration TEXT NOT NULL)"
        )
    security.limiter.bind(db_path)
    if security.production():
        db_path.chmod(0o600)
