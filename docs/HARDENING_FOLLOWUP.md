# Hardening follow-up — existing branch and PR

All work continues on `security/platform-hardening` and PR #2. No new branch, merge, public deployment, production credential rotation or history rewrite was performed.

This report supersedes the older implementation report wherever behavior changed. Actual model inference and external production services remain unverified; this is not a claim of complete public-production readiness.

## Completed corrections

| Area | Current behavior | Evidence |
|---|---|---|
| Scan findings | age receives only generated staging paths; recipient/identity/archive data are staged before execution. Launcher uses a fixed loopback HTTPConnection; browser fixture readiness uses TCP. No Semgrep suppressions. | Expanded Semgrep scan: zero findings and parsing errors. Raw results in validation/semgrep.json. |
| Reference correctness | Explicit reference IDs retrieve their immutable transcript version. Language-specific uploads are supported. Trusted shared-template deduplication cannot promote private uploads. Legacy unowned profile fallback cannot disclose its transcript. | Regression exercises old reference after a newer upload, English generation, and unowned legacy profile access. |
| Worker execution | Persistent spawned processes isolate each TTS engine and the evaluator. Deadlines include worker-lock waits. Timeout/cancellation terminates and joins the real process; a later request recovers with a fresh process. Parent-death monitoring and systemd control-group termination cover service shutdown. | Real synthetic subprocess timeout, crash/restart and cancellation tests; API-level owned cancellation/timeout/recovery test. No real TTS execution claimed. |
| Startup/shutdown | Data-directory lock prevents two servers using one store. Lock remains held through worker cleanup. Launcher preloading occurs after lifespan acquires the lock. Verify-only uses bounded workers. | Lock reentrancy/exclusion regression plus process supervision tests. |
| Database | New stores use real foreign keys, primary-key/non-null constraints and query indexes. Legacy production stores require an explicit offline migration, with a consistent private pre-migration snapshot and transaction rollback. Migration refuses unknown columns and invalid relationships. | Successful preservation/enforcement test and invalid-data rollback test. `schema.py --apply` also requires the data-directory lock. |
| Storage | Atomic byte reservations enforce per-user/global/disk-free capacity, alongside count limits. Outputs have byte, duration, rate, channel and frame limits. Reservations remain until execution terminates. | Byte-limit rollback and process cancellation tests. |
| Exports | ZIPs are disk-backed, have a size limit and two concurrent slots, and reserve temporary disk capacity. Response completion/errors/disconnect cleanup removes temporary files and reservations. | Existing ZIP contents/settings/download tests also verify cleanup and released reservations. |
| Login | Source/account limits are atomic SQLite records that survive restart. Argon2 verification concurrency is bounded. Session count is bounded per principal. Login security events are retained without credentials or raw IP keys. | Persistent-limit recreation, account/session and role tests. |
| Administration | Accounts mode requires an admin role and separate administrator credential, including development. `/api/admin/security-events` exposes only recent event kind/time/request ID to authorized administrators. | Non-admin request with administrator token is denied. |
| Validation | Input models reject unknown fields and coercion of values such as Boolean ratings. Validation errors omit input values, including passwords/tokens. Non-ASCII incorrect tokens return a normal authentication rejection. | Unknown-owner, Boolean-rating, secret-echo and Unicode token regressions. |
| Browser sessions | Same-origin cookie login remains HttpOnly. Cross-site frontends explicitly request an opaque session token kept only in page memory. Protected audio/download fetches send it in a header. Logout revokes either transport. No token is placed in URLs, localStorage or sessionStorage. | Backend no-cookie session/revocation test. New fixture-based browser cross-site test is part of CI. |
| Controls/provenance | Qwen and XTTS temperature can be adjusted independently while keeping CosyVoice in a comparison. Per-model rate overrides are accepted by the API. Job/cache/export metadata includes effective defaults/sampling/pause settings. | Independent settings regression and existing cache/download/provenance checks. |
| Headers/TLS | Early 401/413 responses also receive no-store, request ID and security headers. Production rejects remote plain HTTP. Built HTML includes a CSP with the configured API origin; Caddy supplies framing/HSTS headers. | Header regressions and production frontend build. Actual public TLS/cookie/CSP behavior still needs staging verification. |
| Backup recovery | Consistent read-only SQLite source; streaming hashes; age encryption/decryption with generated argv; atomic non-overwriting encrypted publication. Restore limits size/member count, rejects links/sparse/duplicate/unsafe members, requires a complete manifest and cleans failed new destinations. | Five backup tests, including encrypted audio/database round trip and tampered ciphertext, traversal, size limit and incomplete manifest rejection. |
| CI/supply chain | Added strict expanded Semgrep job, medium-or-higher Bandit gate and cross-site browser test. GitHub Actions are pinned to observed commit SHAs and checkout does not retain credentials. | Local scan/tests/build; remote CI results are recorded in the PR. |

## Validation on the editing host

- Full backend suite: **94 passed**, including actual synthetic worker processes and actual loopback HTTP authentication tests. The latest complete run finished in 12.79 seconds with one Starlette/httpx deprecation warning.
- Backup suite after the final read-only snapshot adjustment: **5 passed**.
- Schema suite after adding the offline migration lock: **2 passed**.
- Frontend TypeScript check and production build pass; npm audit reports zero vulnerabilities.
- Expanded Semgrep policies: repository rules plus `p/security-audit`, `p/python`, `p/javascript`, `p/typescript`; zero findings and no parsing errors. Registry rules remain externally maintained.
- Bandit: **0 high, 0 medium, 16 low** audit results. Remaining low results concern subprocess imports/argv and exception handling in operator-controlled tools; they are retained in the full report, not suppressed.
- Gitleaks working-tree scan: no leaks detected. Final history scan is also recorded in the PR.
- No production TTS/evaluator, CUDA, Windows runtime, public server, external backup host or alert destination was available for genuine operational validation.

Commands:

```bash
python -m pytest backend/tests -q
python -m compileall -q backend start.py
python -m bandit -r backend start.py -x backend/tests -ll
semgrep scan --config .semgrep.yml --config p/security-audit --config p/python --config p/javascript --config p/typescript --metrics=off --no-git-ignore --error .
# frontend directory
npm ci
npm run build
npm run test:responsive
node tests/cross_site.cjs
npm audit --audit-level=moderate
```

Encrypted recovery integration requires age and age-keygen on PATH. CI installs the checksum-verified age release for that dedicated job. Without age, that one integration test explicitly skips; it is not counted as passing.

## Upgrade an existing private deployment

1. Stop the old service and preserve a verified encrypted database/audio backup.
2. On an isolated copy first, inspect `python backend/schema.py --database /absolute/data/studio.sqlite3`.
3. Apply explicitly: `python backend/schema.py --database /absolute/data/studio.sqlite3 --apply`. The original snapshot is private and must be retained securely. Invalid legacy rows or unknown columns abort; no rows are silently discarded.
4. Keep `INFERENCE_ISOLATION=process` in production, review the output/time/storage limits in `.env.example`, and provision the actual model environment/checkpoints before startup.
5. Configure a distinct account per user for isolation. Token mode intentionally identifies one shared workspace principal.
6. Build frontend assets with the intended `VITE_API_URL`. Cross-site deployments use the new memory-only session transport; same-origin deployments retain cookie sessions.
7. Verify actual TLS, proxy trust, account isolation, worker memory use, reference/transcript correctness, daily off-host restore and alerts in staging before public release.

## Remaining external or empirical work

GitHub Dependency Review remains blocked by disabled Dependency graph; available connector operations cannot enable that repository administration setting. The check has not been removed or bypassed. Enable it in repository security analysis settings and rerun the job.

The actual TTS/evaluator package/checkpoint/GPU/Windows environment is still not validated or fully locked. No trained-model, naturalness, numerical quality or latency improvement is claimed. A genuinely randomized/blinded multi-rater research workflow and empirical benchmark results are not delivered by these security fixes. The listening UI no longer claims unbiased randomized A/B/X evaluation, and predicted ScoreQ quality is distinguished from human ratings.

Daily backup timers, remote storage, alert delivery, production TLS/firewall/service policies and branch protections still require configuration and verification on their owning systems. No such services have been activated from this environment.

If the operating system cannot confirm worker termination, the worker is never reused and the job/storage reservation remains held; cancellation returns a service error rather than falsely reporting success. Such kernel/host failures require administrator intervention. This fail-closed invariant is tested with a synthetic unstoppable process; it is not a guarantee that software can stop an uninterruptible native kernel task.
