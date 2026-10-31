"""Resolve configured immutable revisions, never select snapshots by modification time."""
import os

def resolve_snapshot(repo, revision, target, label):
    from huggingface_hub import snapshot_download
    production = os.getenv('APP_ENV') == 'production'
    if production and (not revision or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision)):
        raise RuntimeError(label + ' requires a reviewed 40-character revision in production')
    marker = target / '.studio-revision'
    if (target / 'config.json').is_file():
        if not revision and not production:
            return str(target)
        if marker.is_file() and marker.read_text().strip() == revision:
            return str(target)
        raise RuntimeError(label + ' local weights do not match the configured revision; provision a verified snapshot')
    return snapshot_download(repo_id=repo, revision=revision or 'main', local_files_only=production)
