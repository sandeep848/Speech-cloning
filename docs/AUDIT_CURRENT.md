# Current repository audit

Date: 2026-11-21. Branch baseline: a21f846bd3e4ecebd1f6555ab131e8902de0eafa.

Scope: repository inventory, backend/application/security/database code, model adapters/evaluator/pacing, startup/profile/benchmark scripts, frontend source and browser fixtures, dependencies and workflows were inspected. Lightweight tests exercised the existing workflows. This is not a claim that every possible runtime path or production vulnerability was assessed. No destructive live-service testing was conducted.

| Severity | File/function and confirmed cause | Reproduction and impact | Correction/evidence |
| --- | --- | --- | --- |
| Critical | app/security/session.py authenticated returned True without API_TOKEN | Clear API_TOKEN outside production and request /api/models; protected data was accessible | Deny by default, fail startup, require explicit loopback development flags; missing-token/configuration/client-boundary tests |
| High | backend.py login/logout used signed timestamp cookies with no revocation | Save a cookie, log out, replay the saved cookie | Persistent random sessions, hash-only storage, expiry and token-rotation validation; replay/rotation/expiry tests |
| High | backend.py upload_reference only checked WAV headers/duration | Remove PCM bytes while leaving the declared frame count; malformed audio was accepted | Full PCM payload validation and supported channels/rate/width; truncated-WAV regression |
| High | Multipart bodies were bounded only after FastAPI had parsed/spooled them | Send a chunked oversized multipart request; parser storage could grow before handler limit | ASGI streamed 17 MiB body boundary plus 16 MiB WAV limit; no-content-length body regression |
| High | execute_worker wrote directly to final output filename | Interrupt a write or terminate process before status update | Temporary WAV plus validation/atomic rename; restart final-output reconciliation and atomic failure tests |
| High | load_backend_reference marked every interrupted job failed and discarded resumable input context | Restart with pending/running jobs | Persisted inputs/attempts, startup reconciliation, three total attempts and explicit legacy failure; recovery/exhaustion/final-output tests |
| Medium | generate had no persistent submission identity | Repeat a successful submission after a client timeout | Optional Idempotency-Key, request fingerprint and job/response transaction; replay and changed-body conflict tests |
| Medium | Model failures and evaluation errors included raw exception messages | Raise an exception containing a private hostname/path | Sanitized persisted inference/evaluation messages; timeout regression verifies private text is absent |
| Medium | No login/write rate limit | Repeated token guesses or rapid submissions | Bounded client-address windows; capacity/expiry tests |
| Medium | frontend main.tsx mixed bootstrap, presets, metrics, composition and polling | Change language or unmount with in-flight requests; discovery intervals could overlap | Feature/type/App extraction, AbortController cleanup and sequential discovery; TypeScript/build pass; browser runtime check blocked |
| Medium | No snapshot/restore utility or verified relocation/checksums | Copy database/audio then restore at a different absolute path | Offline SQLite backup and verified restore; roundtrip/corruption/overwrite tests |
| Medium | model-release.yml was an independent optional workflow; Pages build did not depend on it | Dispatch deployment after CPU checks without real-model evidence | Manual build now requires model-release and full installed-runtime audit; configuration inspected, not dispatched |
| Low | status document requested separate workers/PostgreSQL despite current architecture | Read previous remaining-work table | Rewritten to require bounded in-process recovery and retain SQLite |

## Remaining confirmed limitations and suspected risks

- Shared-token access has no individual identity or resource ownership. This is a confirmed limitation, not a fixed tenant-isolation feature. Untrusted multi-user exposure remains a release blocker.
- Actual model, dependency and metric compatibility are unverified on approved hardware. Package presence is not inference evidence. Readiness now requires loaded engines, but a loaded engine is still not a quality benchmark.
- Full backend route/service/repository separation and frontend feature/component extraction are incomplete. The remaining large files are documented rather than disguised behind compatibility wrappers.
- General foreign-key migration, rollback migrations, retention policy, all failure-injection cases and cross-user tests remain incomplete. The job-input migration is additive and version recorded.
- A forced stop can leave temporary audio; it is not served as completed output. Automatic cleanup/retention is deliberately absent. Upload audio and transcript are individually atomically written, but the pair is not one crash-atomic transaction. External edits to reference files must be coordinated.
- Model locks prevent simultaneous use of the same adapter; the shared executor permits up to three model threads. GPU memory adequacy for that concurrency has not been established. A resource-driven concurrency policy remains needed on the real host.
- Restart-reconciled audio has no fabricated runtime value. Interrupted evaluations remain recoverable through an explicit evaluation request; automatic backfill scheduling is incomplete.
- Full baseline provenance/checkpoint pinning, calibrated automatic-score ranges and human listening evidence remain incomplete. Existing frontend quality labels are heuristics, not approved empirical thresholds.
- Some legacy model availability errors may include setup details. Secure centralized error-monitoring/alerting and comprehensive logging for early rejected requests remain incomplete.
- Existing model timing records remain process-local; first-audio timing is not fully durable across restarts/cache reuse.
- No complete installed ML-runtime audit, full local secrets-history scan, live staging validation, hosted CI confirmation or deployed rollback rehearsal was possible here.

## Static and dependency findings

API/test resolved dependency audit: no known vulnerabilities. npm audit: zero reported vulnerabilities. These observations apply to the resolved scan at execution time, not future advisories or the full ML runtime.

Bandit: zero high, three medium and four low findings. Medium B615 findings are existing unpinned Hugging Face downloads in evaluator.py, models/qwen3/model.py and models/cosyvoice3/model.py. Low findings are the existing broad CUDA probe fallback and CosyVoice subprocess setup/partial command path. Downloads/optional setup need approved immutable checkpoint/source manifests and host-level review. No findings were hidden with blanket suppressions. Fixed-query relocation removed dynamic-SQL false positives.

## Removed code

Removed duplicate inline request-schema definitions after compatibility re-export, moved inline frontend types/presets/metric definitions into modules, replaced the old signed-cookie implementation and removed the unconditional interrupted-generation failure update. No model adapter, public compatibility helper, feature, private recording or generated asset was deleted. No broad dead-code deletion was attempted without proven reference coverage.
