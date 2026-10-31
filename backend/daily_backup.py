"""Daily orchestration: encrypted local snapshot, configured off-host copy, retention."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone


def main():
    data=Path(os.environ['VOICE_STUDIO_DATA'])
    backups=Path(os.environ['BACKUP_DIR'])
    # JSON argv is administrator configuration, never a shell command or user input.
    copy=json.loads(os.environ['BACKUP_COPY_ARGV'])
    if not isinstance(copy,list) or not copy or not all(isinstance(x,str) for x in copy):
        raise RuntimeError('BACKUP_COPY_ARGV must be an argv JSON array with {archive}')
    if not any('{archive}' in x for x in copy):
        raise RuntimeError('Off-host backup command must contain {archive}')
    backups.mkdir(mode=0o700,parents=True,exist_ok=True)
    archive=backups/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.age')
    subprocess.run([sys.executable,str(Path(__file__).with_name('backup.py')),'backup','--data',str(data),'--archive',str(archive)],check=True)
    subprocess.run([x.replace('{archive}',str(archive)) for x in copy],check=True)
    # Delete only this application's dated encrypted backups, after off-host success.
    cutoff=time.time()-int(os.getenv('BACKUP_RETENTION_DAYS','30'))*86400
    for old in backups.glob('????????T??????Z.tar.age'):
        if old.stat().st_mtime < cutoff:old.unlink()

if __name__=='__main__':
    main()
