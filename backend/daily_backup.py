"""Daily orchestration: encrypted local snapshot, configured off-host copy, retention."""

import json
import os
from pathlib import Path
import subprocess
import time
from backup import create_encrypted_snapshot
from datetime import datetime, timezone


def main():
    data = Path(os.environ["VOICE_STUDIO_DATA"])
    backups = Path(os.environ["BACKUP_DIR"])
    # JSON argv is administrator configuration, never a shell command or user input.
    copy = json.loads(os.environ["BACKUP_COPY_ARGV"])
    if (
        not isinstance(copy, list)
        or not copy
        or not all(isinstance(x, str) for x in copy)
    ):
        raise RuntimeError("BACKUP_COPY_ARGV must be an argv JSON array with {archive}")
    if not any("{archive}" in x for x in copy):
        raise RuntimeError("Off-host backup command must contain {archive}")
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive = backups / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".tar.age"
    )
    create_encrypted_snapshot(data, archive, os.environ["BACKUP_AGE_RECIPIENT"])
    subprocess.run(
        [x.replace("{archive}", str(archive)) for x in copy], check=True, timeout=600
    )
    # Delete only this application's dated encrypted backups, after off-host success.
    retention = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
    if not 1 <= retention <= 3650:
        raise RuntimeError("BACKUP_RETENTION_DAYS must be between 1 and 3650")
    cutoff = time.time() - retention * 86400
    for old in backups.glob("????????T??????Z.tar.age"):
        if old.stat().st_mtime < cutoff:
            old.unlink()


if __name__ == "__main__":
    main()
