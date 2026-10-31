# Backup and recovery

Target policy for review: daily consistent snapshots, 30-day local retention, encrypted off-host copy, RPO <=24 hours after verified successful daily copies, and target RTO <=4 hours. These are proposed objectives, not measured service guarantees.

`backup.py` uses SQLite's backup API, checks database integrity/foreign keys, collects only referenced immutable audio files and records SHA-256 hashes. Missing/unsafe audio aborts the snapshot. `age` encrypts the archive using an operator-provided public recipient; keep its private identity on a separately secured recovery host. No key is committed or generated automatically. Private temporary files and restrictive permissions are used; use encrypted host disks as plaintext temporary snapshots exist during backup/restore.

```bash
python backend/backup.py backup --data /srv/voice-studio/data --archive /srv/voice-studio/backups/drill.tar.age --recipient age1YOUR_PUBLIC_RECIPIENT
python backend/backup.py restore --data /srv/voice-studio/restore-drill --archive /srv/voice-studio/backups/drill.tar.age --identity /secure/recovery-identity.txt
```

Restore refuses an existing destination, rejects traversal/symlinks, verifies the manifest and database, relocates audio paths and clears sessions/reset tokens. The destination remains private. Start it isolated, check reference playback and owned-record access, and record elapsed time before switching production paths. Database-only rollback is insufficient when audio is missing.

`daily_backup.py` first writes an encrypted archive, invokes administrator-configured off-host copy argv without a shell, and applies dated-file retention only after successful copying. Configure BACKUP_DIR, BACKUP_AGE_RECIPIENT and BACKUP_COPY_ARGV in protected service configuration. Ensure the destination is actually off-host, encrypted in transit and access-restricted. Its own retention policy is separate.

The provided systemd timer runs daily at 02:00 UTC with a small random delay and catches missed runs. Enable it only after a real encryption/decryption and off-host restore drill. The service has an OnFailure hook for `voice-studio-backup-alert.service`; operators must supply and test that alert integration. No message is sent and no alert service is installed by this branch. A nonzero exit and journal record alone are not a verified alert.

Locally, snapshot/restore and malicious-archive tests pass. A checksum-verified age 1.3.2 release also passed genuine encrypted database/audio backup/restore and ciphertext-tampering rejection. Remote copy, alert delivery, retention under failure and measured RTO remain unverified. The scheduled service has not been installed or activated.
