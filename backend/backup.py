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
from datetime import datetime, timezone


def snapshot(root: Path, destination: Path):
    root, destination = root.resolve(), destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with sqlite3.connect(root/'studio.sqlite3') as source, sqlite3.connect(destination/'studio.sqlite3') as target:
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
        manifest['audio/'+path.name] = hashlib.sha256(copied.read_bytes()).hexdigest()
    manifest['studio.sqlite3'] = hashlib.sha256((destination/'studio.sqlite3').read_bytes()).hexdigest()
    (destination/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))


def verify_and_relocate(root: Path):
    manifest = json.loads((root/'manifest.json').read_text())
    for name, digest in manifest.items():
        target = (root/name).resolve()
        if not target.is_relative_to(root.resolve()) or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Backup integrity validation failed')
    with sqlite3.connect(root/'studio.sqlite3') as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Restored database integrity check failed')
        for table,column in [('references_audio','path'),('jobs','output')]:
            for identifier,path in c.execute(f'SELECT id,{column} FROM {table} WHERE {column} IS NOT NULL').fetchall():
                relocated = root/'audio'/Path(path).name
                if not relocated.is_file():
                    raise RuntimeError('Restored audio unavailable')
                c.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(str(relocated),identifier))
        c.execute('DELETE FROM sessions')
        c.execute('DELETE FROM password_resets')
    for path in root.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)


def restore_archive(archive: Path, root: Path):
    if root.exists():
        raise RuntimeError('Restore destination must not exist')
    root.mkdir(mode=0o700, parents=True)
    with tarfile.open(archive,'r') as tar:
        for item in tar.getmembers():
            target=(root/item.name).resolve()
            if not target.is_relative_to(root.resolve()) or not (item.isfile() or item.isdir()):
                raise RuntimeError('Unsafe archive member')
        tar.extractall(root, filter='data')
    verify_and_relocate(root)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','restore'])
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--recipient',default=os.getenv('BACKUP_AGE_RECIPIENT'))
    parser.add_argument('--identity',type=Path)
    args=parser.parse_args()
    age=shutil.which('age')
    if not age:
        raise RuntimeError('Install age before backing up or restoring encrypted data')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='studio-backup-') as tmp:
        tmp=Path(tmp)
        tar_path=tmp/'snapshot.tar'
        if args.action=='backup':
            if not args.recipient:
                raise RuntimeError('BACKUP_AGE_RECIPIENT is required')
            if args.archive.exists():
                raise RuntimeError('Archive already exists')
            snapshot(args.data,tmp/'snapshot')
            with tarfile.open(tar_path,'w') as tar:
                for file in (tmp/'snapshot').iterdir():
                    tar.add(file,arcname=file.name)
            args.archive.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            partial=args.archive.with_suffix('.partial')
            try:
                subprocess.run([age,'-r',args.recipient,'-o',str(partial),str(tar_path)],check=True)
                partial.replace(args.archive)
            finally:
                partial.unlink(missing_ok=True)
        else:
            if not args.identity:
                raise RuntimeError('--identity is required for restore')
            subprocess.run([age,'-d','-i',str(args.identity),'-o',str(tar_path),str(args.archive)],check=True)
            restore_archive(tar_path,args.data.resolve())
    print('Backup operation completed')

if __name__=='__main__':
    main()
