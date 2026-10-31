# Known limitations and release blockers

Release status: **Not ready** for the complete requested public multi-user platform.

- Real Qwen, CosyVoice, XTTS and evaluator weights were not executed. No GPU, naturalness, latency or metric improvement is claimed. Removal of the global unsafe Torch-loading override can expose upstream checkpoint incompatibility requiring a narrowly reviewed remedy.
- Model dependencies, executable CosyVoice source, all checkpoint hashes and GPU/Windows compatibility are not locked/validated. Existing dependency ranges are not a production lockfile. The API/test lock is separate.
- Local encrypted database/audio recovery and ciphertext-integrity rejection pass; off-host scheduled operation is still unverified.
- Browser responsive/security flows could not be executed: Chromium download returned a truncated archive. The frontend builds, but visual correctness, cookies on actual Pages and final CSP require browser/staging tests.
- HTTPS, reverse proxy, filesystem/service hardening, off-host encrypted backups, restore alerts, retention and public token/session rotation have not been validated on a deployed server. Templates are not active services.
- Gitleaks found no leaks in the current working tree. The separate history scan and full model dependency audit are reported in IMPLEMENTATION_REPORT.md; automated scans cannot prove absence of vulnerabilities.
- Automatic approval review blocked Semgrep because it could contact semgrep.dev and disclose repository/scan data. The scan is unverified and was not retried or moved into an automatic CI job.
- SQLite legacy tables retain incomplete foreign keys/constraints. Ownership is enforced consistently by application routes and the new ownership tables, but full relational schema migration/recovery and a reviewed data-retention/deletion workflow remain incomplete.
- Existing unowned data is quarantined; no production data was copied, assigned or deleted. Per-user storage quotas bound counts, not total output bytes. Legacy or benchmark data can exceed those quotas.
- Thread-based inference cannot safely terminate a hung native model or reclaim GPU resources. Hard timeouts, real cancellation, complete restart-under-load recovery, GPU monitoring and process-isolated jobs remain outstanding. Never release a semaphore while an underlying inference thread is still running.
- One API worker/service is required. Rate limits are in-memory, reset on restart and are not a distributed WAF. Login has source/account quotas and generic errors; progressive delays, monitoring dashboards and comprehensive compromised-password screening are incomplete.
- Registration is intentionally absent; users are provisioned offline. Password reset is an administrator-issued one-use token flow, without automated email. No maintained external identity provider has been integrated.
- The account role field and production admin-reference reload check are a narrow role gate, not a full administrative product. Review role coverage before expanding the API.
- Research batches defer starting inference until every child passes planning; failed planning removes new rows. Crash recovery during batch planning still needs a persisted parent state.
- Request controls use the adapters' existing parameter contracts. CosyVoice temperature is unsupported. Per-model temperature UI, full effective-parameter provenance, pause overrides and complete model-revision cache identity require further work.
- Blinded/randomized listening tests, a validated expanded German benchmark and empirical research deliverables remain unperformed.
- Public Git history already contains reference audio/transcripts. API security does not hide GitHub-hosted files. Consent/privacy review and any history action require explicit operator authorization.
- Branch protections and required checks are not configured automatically. Review the candidate PR before merging. Do not enable production deployment until blockers relevant to its scope are resolved.
