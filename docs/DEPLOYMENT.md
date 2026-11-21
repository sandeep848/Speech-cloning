# Run, release and recover

Use the existing `issues-and-fixes` branch and your `qwen3` environment. The full model environment has not been validated here. Install runtime requirements in that environment only; investigate resolution failures rather than creating model services.

```bash
cd Speech-cloning
git switch issues-and-fixes
conda activate qwen3
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

Configure backend/.env from backend/.env.example with a private random API_TOKEN and persistent absolute VOICE_STUDIO_DATA and REFERENCE_DIR paths. The application login uses that shared access token. Do not put tokens in VITE variables. For an explicitly local, token-free development session:

```bash
export STUDIO_ENV=development
export ALLOW_INSECURE_LOCAL=1
export PRELOAD_MODELS=0
cd backend
python -m uvicorn backend:app --host 127.0.0.1 --port 8000 --workers 1
```

PRELOAD_MODELS=0 delays model loading until generation; it does not disable models. The built frontend is served by the same backend after restart. For a GPU host, keep the same command and environment, configure MODEL_DEVICE=cuda:0 and enable preload if desired. GPU execution and model compatibility must pass the real-hardware gate before release.

For staging/production use STUDIO_ENV=staging or production, remove ALLOW_INSECURE_LOCAL, provide API_TOKEN of at least 32 characters, COOKIE_SECURE=1, WEB_CONCURRENCY=1 and explicit HTTPS CORS_ORIGINS. Bind the backend to loopback behind the TLS proxy. Adapt deploy/Caddyfile and deploy/voice-studio.service; increase TimeoutStopSec for real inference duration. Never run multiple backend instances against the same data directory. HTTPS termination and access restrictions must be verified on the actual host.

GitHub Pages requires VITE_API_URL set at frontend build time to the backend's HTTPS origin. CORS_ORIGINS contains the frontend origin, e.g. https://sandeep848.github.io, without the repository path. Check browser cookie policy for cross-site hosting. Models, reference audio and the database are never hosted by Pages.

## Release prerequisites

The manual Pages workflow still accepts main only; it has not been executed and main is not modified by this work. Its build now depends on both central verification and real-model validation. That validation uses the trusted self-hosted labels voice-studio-models/linux/x64 and the model-validation environment. Configure that environment's reviewers, runner, reference-file variables and complete Python runtime plus pip-audit. The github-pages environment also needs required reviewers in repository settings. Files alone do not enforce those administrator settings.

Routine CI uses hosted CPU runners. It does not execute expensive TTS inference. Review the exact SHA's Final checks, model-smoke report, full installed-runtime audit/SBOM, static/security findings, staging evidence and backup before approving a release. No backend target or automatic backend production deployment is configured.

## Offline backup and restoration

Stop admissions, wait for inference/evaluation to finish, and stop the API. The --service-stopped flag is an operator assertion; the script cannot prove that no other process is writing. Keep snapshots private and outside Git.

```bash
python scripts/backup.py backup /srv/voice-studio/data /srv/backups/voice-studio-2026-11-21 --service-stopped
python scripts/backup.py restore /srv/backups/voice-studio-2026-11-21 /srv/voice-studio/restored-data --service-stopped
```

Backup uses SQLite's backup API and copies referenced immutable audio with checksums. Restore verifies checksums/integrity, refuses an existing destination, and relocates stored reference/output paths. Separately protect backend/.env, external reference-directory audio/transcript pairs, checkpoint versions and runtime lock/provenance. Snapshots include private data and session state; secure permissions and retention are operator responsibilities. No automatic deletion policy was introduced.

## Staging and rollback rehearsal

Provision isolated staging with synthetic/authorized data, HTTPS and its own token/data paths. Test readiness, login/logout/replay, references, all models, metrics, audio downloads, exports, ratings, duplicate submissions, restart and backup restore. Readiness returns 503 until all three actual model engines are loaded; importability alone does not count as loaded. It is protected by API authentication and is not a voice-quality test.

Record the previous release SHA, Python/package and checkpoint versions, database/audio snapshot and frontend artifact. To roll back: stop admissions, drain/stop the API, restore the previous code and environment; when schema/data compatibility requires it, restore the matching database/audio snapshot into a new directory and switch VOICE_STUDIO_DATA. Rebuild/restore the previous frontend artifact if its contract changed. Verify readiness and an authorized real generation before reopening access. Reverting code does not undo migrations. No live staging or rollback rehearsal was possible in this environment.
