"""Emit traceable CI metadata without serializing secrets or user recordings."""
import datetime
import json
import os
import subprocess

print(json.dumps({
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "actor": os.getenv("BUILD_ACTOR"),
    "event": os.getenv("BUILD_EVENT"),
    "run_id": os.getenv("GITHUB_RUN_ID"),
    "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
    "repository": os.getenv("GITHUB_REPOSITORY"),
    "ref": os.getenv("GITHUB_REF"),
    "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, indent=2))
