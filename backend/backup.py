"""Consistent SQLite/audio snapshots, encrypted with the maintained age tool.

The service uses immutable audio. Database backup pins the exact files to copy.
Restore always targets a NEW directory and rejects traversal and symlinks.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path, destination: Path):
    root, destination = root.resolve(), destination.resolve()
    if not (root/'studio.sqlite3').is_file():
        raise RuntimeError('Source database does not exist')
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with sqlite3.connect((root/'studio.sqlite3').as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(destination/'studio.sqlite3') as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or target.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Database integrity check failed')
        paths = [row[0] for row in target.execute('SELECT path FROM references_audio UNION SELECT output FROM jobs WHERE output IS NOT NULL')]
    (destination/'audio').mkdir(mode=0o700)
    manifest = {}
    for original in paths:
        path = Path(original).resolve()
        if not path.is_relative_to(root/'audio') or not path.is_file():
            raise RuntimeError('Missing or unsafe audio path in snapshot')
        copied = destination/'audio'/path.name
        shutil.copyfile(path, copied)
        manifest['audio/'+path.name] = digest_file(copied)
    manifest['studio.sqlite3'] = digest_file(destination/'studio.sqlite3')
    (destination/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))


def verify_and_relocate(root: Path):
    if (root/'manifest.json').stat().st_size > 8*1024*1024:
        raise RuntimeError('Backup manifest exceeds size limit')
    manifest = json.loads((root/'manifest.json').read_text())
    files={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if not isinstance(manifest,dict) or 'studio.sqlite3' not in manifest or set(manifest) != files:
        raise RuntimeError('Backup manifest does not describe every restored file')
    for name, digest in manifest.items():
        target = (root/name).resolve()
        if not target.is_relative_to(root.resolve()) or digest_file(target) != digest:
            raise RuntimeError('Backup integrity validation failed')
    with sqlite3.connect(root/'studio.sqlite3') as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Restored database integrity check failed')
        for select,update in (
            ('SELECT id,path FROM references_audio WHERE path IS NOT NULL','UPDATE references_audio SET path=? WHERE id=?'),
            ('SELECT id,output FROM jobs WHERE output IS NOT NULL','UPDATE jobs SET output=? WHERE id=?'),
        ):
            for identifier,path in c.execute(select).fetchall():
                relocated = root/'audio'/Path(path).name
                if not relocated.is_file():
                    raise RuntimeError('Restored audio unavailable')
                c.execute(update,(str(relocated),identifier))
        has_ledger = c.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_sizes'").fetchone()
        if has_ledger:
            rows = c.execute('SELECT path FROM artifact_sizes').fetchall()
            for (old_path,) in rows:
                c.execute('UPDATE artifact_sizes SET path=? WHERE path=?', (str(root/'audio'/Path(old_path).name), old_path))
            c.execute('DELETE FROM storage_reservations')
        c.execute('DELETE FROM sessions')
        c.execute('DELETE FROM password_resets')
    for path in root.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)


def restore_archive(archive: Path, root: Path):
    if root.exists():
        raise RuntimeError('Restore destination must not exist')
    root.mkdir(mode=0o700, parents=True)
    try:
        with tarfile.open(archive,'r') as tar:
            total, members, names = 0, [], set()
            for item in tar:
                target=(root/item.name).resolve()
                if not target.is_relative_to(root.resolve()) or not (item.isfile() or item.isdir()) or item.issparse():
                    raise RuntimeError('Unsafe archive member')
                if target in names:
                    raise RuntimeError('Duplicate archive member')
                names.add(target)
                total+=item.size
                if total > int(os.getenv('MAX_RESTORE_BYTES','17179869184')) or len(members)>=50000:
                    raise RuntimeError('Restore exceeds configured limits')
                members.append(item)
            tar.extractall(root, members=members, filter='data')
        verify_and_relocate(root)
    except BaseException:
        shutil.rmtree(root)
        raise


def create_encrypted_snapshot(data: Path, archive: Path, recipient: str):
    """Only generated temporary paths enter age's argv; operator inputs are data."""
    age = shutil.which('age')
    if not age:
        raise RuntimeError('Install age before backing up encrypted data')
    if not recipient or "\n" in recipient or "\r" in recipient or len(recipient) > 1000:
        raise ValueError('A single valid age recipient is required')
    data, archive = data.resolve(), archive.resolve()
    if archive.exists():
        raise RuntimeError('Archive already exists')
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='studio-backup-') as temporary:
        staging = Path(temporary)
        snapshot(data, staging / 'snapshot')
        with tarfile.open(staging / 'snapshot.tar', 'w') as tar:
            for item in (staging / 'snapshot').iterdir():
                tar.add(item, arcname=item.name)
        recipients = staging / 'recipients.txt'
        recipients.write_text(recipient + "\n", encoding='utf-8')
        # Recipient contents are parsed by age, never interpreted as CLI options.
        subprocess.run([age, '--encrypt', '--recipients-file', str(recipients),
                        '--output', str(staging / 'snapshot.age'), str(staging / 'snapshot.tar')],
                       check=True, timeout=600)
        # Publish the complete encrypted file atomically without replacing a race winner.
        descriptor, temporary = tempfile.mkstemp(prefix='.studio-', suffix='.age', dir=archive.parent)
        staged_archive = Path(temporary)
        try:
            with os.fdopen(descriptor, 'wb') as output, (staging / 'snapshot.age').open('rb') as source:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(staged_archive, archive)
            except FileExistsError:
                raise RuntimeError('Archive already exists') from None
        finally:
            staged_archive.unlink(missing_ok=True)



def restore_encrypted_snapshot(archive: Path, data: Path, identity: Path):
    age = shutil.which('age')
    if not age:
        raise RuntimeError('Install age before restoring encrypted data')
    if data.exists():
        raise RuntimeError('Restore destination must not exist')
    with tempfile.TemporaryDirectory(prefix='studio-restore-') as temporary:
        staging = Path(temporary)
        shutil.copyfile(identity, staging / 'identity.txt')
        shutil.copyfile(archive, staging / 'snapshot.age')
        subprocess.run([age, '--decrypt', '--identity', str(staging / 'identity.txt'),
                        '--output', str(staging / 'snapshot.tar'), str(staging / 'snapshot.age')],
                       check=True, timeout=600)
        restore_archive(staging / 'snapshot.tar', data.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--recipient', default=os.getenv('BACKUP_AGE_RECIPIENT'))
    parser.add_argument('--identity', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == 'backup':
        create_encrypted_snapshot(args.data, args.archive, args.recipient)
    else:
        if not args.identity:
            parser.error('--identity is required for restore')
        restore_encrypted_snapshot(args.archive, args.data, args.identity)
    print('Backup operation completed')


if __name__ == '__main__':
    main()
