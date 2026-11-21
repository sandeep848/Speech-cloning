# Implementation status — issues-and-fixes

Baseline: `a21f846bd3e4ecebd1f6555ab131e8902de0eafa`.
Protected main recorded before work: `3fc34cba90fa446f59e1a47e7bbf1da238bc2d41`.

| Area | Status and evidence |
| --- | --- |
| Authentication | Implemented/tested: missing configuration fails closed; explicit loopback development; revocable persistent sessions; expiry, token rotation and replay regressions |
| API boundaries | Implemented/tested: write/login rate limits, streamed body and PCM validation; safe inference/evaluation errors; CORS and existing session behavior preserved |
| Job reliability | Implemented/tested: private persisted inputs, bounded admission, atomic output, idempotency, bounded transient retries, restart reconciliation, retry exhaustion and pending cancellation |
| Database/storage | SQLite retained; additive version record/indexes; rollback helper retained; offline backup, checksums, restore relocation and corruption rejection tested |
| Backend organization | Config, schemas, security, job repository and audio/backup services extracted; compatibility entry points retained. Most HTTP routes and orchestration still remain in backend.py |
| Frontend organization | Bootstrap, App, error boundary, shared types, presets and metric definitions separated; polling is sequential and aborts on cleanup; TypeScript/build pass. Broader feature/component extraction remains |
| CI/release | Integration component included in fail-closed final coordinator; runtime SBOM/audit and actual model/metric smoke checks wired into manual deployment prerequisites. Hosted execution not yet verified |
| Observability | Structured accepted-request and inference failure logs, request IDs, health and protected loaded-model readiness implemented; external monitoring/alerts remain |
| Real models/metrics | Lightweight regressions pass. Actual three-model inference, complete runtime compatibility, metric validity and performance remain blocked by missing approved runtime/weights/reference data |
| Multi-user isolation | Not implemented; shared-token holders share the workspace. Independent identities and per-resource ownership migration remain required for untrusted multi-user service |
| Staging/production | Configuration validation/templates and offline recovery tooling implemented. No staging host provisioned, no live rollout or rollback rehearsal, no production deployment performed |
| Research quality | Smoke/benchmark tools retained and extended. Approved checkpoint/baseline manifests, calibrated listening and quality regressions remain unverified |
| Coverage | Measured local results are in VERIFICATION.md; the non-test backend coverage target is not met |
| Governance | Existing CODEOWNERS, compatibility checks and Dependabot retained. Required branch checks/environment reviewers need repository administration; no administrative mutation tool is available |

No Celery, Redis, distributed job brokers, separate model services, PostgreSQL migration, Kubernetes or additional permanent branch was introduced. The previous recommendations for separate workers and unnecessary database migration have been removed.

Release assessment: useful hardening and modularization, **not accepted as production-ready**. Remaining blockers are real inference/evaluation evidence, full runtime dependency/security results, hosted CI/browser verification, further modularization, ownership if multi-user access is needed, and deployed staging/restore/rollback/monitoring validation.
