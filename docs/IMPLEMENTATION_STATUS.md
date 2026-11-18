# Issues and fixes branch: implementation status

## Implemented

- Component workflows for frontend, backend, model/evaluation, security and contracts, coordinated by a final fail-closed status. Triggers include pushes, PRs and merge queues.
- Existing GitHub Pages deployment now checks the full suite first and only accepts main. Feature branches cannot use it to replace the live site. No deployment performed.
- Versioned AST compatibility checks for public Python definitions, request classes and route decorators. Behavioral regressions cover concurrency/cache, reference audio, ratings/batches, authentication and evaluation.
- Initial folder boundary enforcement, CODEOWNERS, PR checklist and weekly dependency-update proposals.
- Session verification and SQLite transaction management extracted from the API module; connection helper explicitly rolls back failures. Browser transport and audio-player presentation extracted from the React application.
- 16 MiB bounded WAV reads, bounded reference transcripts, stable validation messages and fail-closed production startup checks for token length/secure cookies.
- Tests create synthetic reference audio in temporary directories rather than depending on private recordings. Four stale assertions aligned with current native comma prosody, 450 ms paragraph pause and 320 ms legacy join defaults; comma-split batching still tested explicitly.
- Vite lockfile updated from 6.4.4 to 6.4.6 to address the reported audit finding. No broad dependency-major upgrade.
- Removed an unused test import and duplicated extracted implementations. Public/re-exported compatibility helpers retained after regression verification.
- Manual real-model validation workflow for a trusted configured runner; no model audio is uploaded as an artifact.

## Remaining acceptance work from the supplied audit

This branch is an initial compatibility-preserving hardening increment. It does not implement the entire target architecture in the supplied audit and must not be labeled multi-user production-ready.

| Area | Remaining work |
| --- | --- |
| Identity and data isolation | Replace shared token with individual identities and authorization; migrate references, jobs, ratings and exports to explicit owners; test cross-user denial. Current records are shared among authenticated token holders. |
| Database | PostgreSQL deployment, versioned migrations, constraints, upgrade/downgrade and restore tests. Current database remains SQLite. |
| Durable workers | Queue-backed API/worker separation, worker leases, retry/idempotency and restart recovery. Existing restart behavior marks interrupted jobs failed rather than resuming them. Keep one API worker. |
| Full modularization | Extract remaining routing, service, evaluation and application state in tested increments. main.tsx/backend.py remain substantial. |
| API schemas | Versioned response schemas, OpenAPI-derived TypeScript and broader consumer contracts. Existing JSON response behavior is only partly covered by tests. |
| Security | Per-user credentials/session revocation, rate limiting, broader threat testing, full model-runtime dependency lock/audit and review of medium/low static findings. CI audits test/API dependency resolution, not the whole ML runtime. |
| Observability | Structured runtime request/job tracing, protected administrative audit storage, metrics, alerting and retention. CI build provenance is implemented; runtime observability remains limited. |
| Release infrastructure | Immutable full-stack container build, staging target, tested database backups, production promotion, rollback and monitored rollout. Existing CD only serves the frontend on Pages. |
| Models/research | Execute the real-model workflow on supported hardware; validate all evaluation dependencies and listener calibration. Mocked tests do not prove real model compatibility or voice quality. |
| Governance | Admin must activate branch rules and environment reviewers, provision trusted model runner and choose collaborator owners. Configuration files cannot enforce these account settings by themselves. |

No change to main and no production rollout are part of this implementation. Never infer deployment readiness solely from green CPU checks.
