# Fixture coverage

Fixtures are isolated in temporary data directories; test configuration explicitly opts into anonymous test mode only where a legacy regression requires it. Security tests use distinct UUID principals, independently salted Argon2 hashes, revocable session records and synthetic WAVs. No private recordings are added as fixtures.

| Group | Exercised | Remaining master-prompt coverage |
|---|---|---|
| Authentication | Missing/wrong credentials, two accounts, separate admin token, revoked/expired/tampered/replayed cookies, account reset and token rotation, actual HTTP restart. | Full staging roles, disabled-account browser flow, breached-password service, registration (not offered). |
| Audio | Mono/stereo generated WAV, silent/NaN/infinite/empty mock outputs in existing waveform tests, clipped/quiet diagnostic tones, truncated/bad headers, request size limits and stable immutable versions. | Every unsupported encoding/rate/oversized-duration boundary, fuzzing and decoder sandbox tests. |
| Generation | German/English existing adapter contracts, punctuation/paragraph/number boundaries, long/empty scripts, duplicate/unknown models, invalid speeds/unsupported temperature, per-request settings/cache/provenance/download. | Real parameters on installed models, all sampling boundaries, empirical reproducibility. |
| Model doubles | Success, silent/empty/nonfinite output, sample-rate change, OOM fallback, context errors, missing dependencies and unsupported control. | Hard timeout/interruption/worker-kill semantics, real checkpoint failure modes. |
| Database | New schema, two-user ownership/preferences, missing audio recovery failure, snapshot/restoration/foreign keys, failed batch-plan rollback. | Complete legacy schema constraints, crash injection at every migration/planning step, daily encrypted off-host recovery. |
| Capacity | Simultaneous submissions respect global pending bound and failed batch planning starts no inference. | Sustained realistic CPU/GPU saturation, byte quotas, restart under load, storage deletion during evaluation. |
| Browser | TypeScript/build and existing responsive test source preserved. | Local Chromium download failed; GitHub CI passed the existing responsive suite at all ten widths. Actual authenticated deployment/accessibility scenarios remain outstanding. |
