"""Transactional byte reservations for owned audio and temporary exports."""
import os
from pathlib import Path
import shutil

from fastapi import HTTPException


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS artifact_sizes (
        path TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        size INTEGER NOT NULL CHECK(size>=0), kind TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS storage_reservations (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        size INTEGER NOT NULL CHECK(size>=0)
    );
    CREATE INDEX IF NOT EXISTS artifact_sizes_owner ON artifact_sizes(owner_id);
    CREATE INDEX IF NOT EXISTS reservations_owner ON storage_reservations(owner_id);
    ''')


def capacity(c, root, owner, requested):
    used=c.execute('SELECT COALESCE(sum(size),0) FROM artifact_sizes WHERE owner_id=?',(owner,)).fetchone()[0]
    reserved=c.execute('SELECT COALESCE(sum(size),0) FROM storage_reservations WHERE owner_id=?',(owner,)).fetchone()[0]
    if used+reserved+requested > int(os.getenv('MAX_USER_STORAGE_BYTES','1073741824')):
        raise HTTPException(429,'User storage quota reached')
    total_reserved=c.execute('SELECT COALESCE(sum(size),0) FROM storage_reservations').fetchone()[0]
    total=0
    for folder in ('audio','exports'):
        directory=root/folder
        if directory.is_dir():
            total+=sum(item.stat().st_size for item in directory.iterdir() if item.is_file())
    if total+total_reserved+requested > int(os.getenv('MAX_STORAGE_BYTES','10737418240')):
        raise HTTPException(429,'Server storage quota reached')
    if shutil.disk_usage(root).free-total_reserved-requested < int(os.getenv('MIN_FREE_STORAGE_BYTES','67108864')):
        raise HTTPException(503,'Server storage capacity unavailable')


def reserve(c, root, owner, identifier, size):
    capacity(c,root,owner,size)
    c.execute('INSERT INTO storage_reservations VALUES (?,?,?)',(identifier,owner,size))


def record(c, path, owner, kind):
    c.execute('INSERT OR IGNORE INTO artifact_sizes VALUES (?,?,?,?)',(str(path),owner,Path(path).stat().st_size,kind))


def backfill(c, root):
    rows=c.execute("SELECT r.path,o.owner_id,'reference' FROM references_audio r JOIN resource_owners o ON o.resource_id=r.id AND o.kind='reference' UNION SELECT j.output,o.owner_id,'output' FROM jobs j JOIN resource_owners o ON o.resource_id=j.id AND o.kind='job' WHERE j.output IS NOT NULL").fetchall()
    for path,owner,kind in rows:
        resolved=Path(path).resolve()
        if resolved.is_relative_to(root/'audio') and resolved.is_file():
            record(c,resolved,owner,kind)
