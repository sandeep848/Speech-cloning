"""Offline consistent snapshots. Stop API admissions/service before backup/restore."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def backup(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ValueError('Snapshot destination must not exist')
    if not (source / 'studio.sqlite3').is_file():
        raise ValueError('Source database is missing')
    destination.mkdir(parents=True)
    try:
        with sqlite3.connect(source / 'studio.sqlite3') as live, sqlite3.connect(destination / 'studio.sqlite3') as saved:
            if live.execute("SELECT count(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]:
                raise ValueError('Drain jobs and stop the backend before taking an offline snapshot')
            live.backup(saved)
        (destination / 'audio').mkdir()
        manifest = {}
        with sqlite3.connect(destination / 'studio.sqlite3') as saved:
            for query in ('SELECT path FROM references_audio', 'SELECT output FROM jobs WHERE output IS NOT NULL'):
                for (value,) in saved.execute(query):
                    original = Path(value)
                    checksum = _digest(original)
                    name = original.name
                    if name in manifest and manifest[name] != checksum:
                        raise ValueError('Conflicting audio filenames')
                    shutil.copy2(original, destination / 'audio' / name)
                    manifest[name] = checksum
        manifest['studio.sqlite3'] = _digest(destination / 'studio.sqlite3')
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    except BaseException:
        shutil.rmtree(destination)
        raise


def restore(snapshot, destination):
    snapshot, destination = Path(snapshot), Path(destination)
    if destination.exists():
        raise ValueError('Restore destination must not exist')
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    if 'studio.sqlite3' not in manifest:
        raise ValueError('Database checksum missing')
    for name, checksum in manifest.items():
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid snapshot path')
        source = snapshot / name if name == 'studio.sqlite3' else snapshot / 'audio' / name
        if _digest(source) != checksum:
            raise ValueError('Snapshot checksum mismatch')
    shutil.copytree(snapshot, destination)
    try:
        with sqlite3.connect(destination / 'studio.sqlite3') as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
            for select_query, update_query in (
                ('SELECT id,path FROM references_audio WHERE path IS NOT NULL', 'UPDATE references_audio SET path=? WHERE id=?'),
                ('SELECT id,output FROM jobs WHERE output IS NOT NULL', 'UPDATE jobs SET output=? WHERE id=?'),
            ):
                for identifier, old in c.execute(select_query).fetchall():
                    name = Path(old).name
                    if name not in manifest:
                        raise ValueError('Referenced audio missing from manifest')
                    c.execute(update_query, (str(destination.resolve() / 'audio' / name), identifier))
        # The stored manifest describes the original snapshot, not this relocated database.
        (destination / 'manifest.json').unlink()
    except BaseException:
        shutil.rmtree(destination)
        raise
