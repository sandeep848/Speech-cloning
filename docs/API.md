# API inventory

Authentication is checked by middleware before private endpoints. Foreign private object IDs return 404. Mutating cookie-authenticated requests require `X-Studio-CSRF: 1`.

| Method | Route | Handler | Source line |
|---|---|---|---|
| POST | `/api/admin/reload-reference` | `reload_backend_reference` | 310 |
| GET | `/api/admin/security-events` | `security_events` | 316 |
| GET | `/api/health` | `health` | 365 |
| GET | `/api/models` | `models` | 375 |
| GET | `/api/reference-profile` | `get_reference_profile` | 429 |
| POST | `/api/references/upload` | `upload_reference` | 477 |
| GET | `/api/references/{reference_id}/audio` | `reference_audio` | 529 |
| DELETE | `/api/jobs/{job_id}` | `cancel_job` | 763 |
| POST | `/api/generations` | `generate` | 781 |
| GET | `/api/generations/{batch_id}` | `batch` | 988 |
| GET | `/api/recordings/{job_id}/audio` | `recording` | 1016 |
| GET | `/api/experiments` | `experiments` | 1028 |
| GET | `/api/evaluations/{job_id}` | `evaluations` | 1038 |
| POST | `/api/evaluations/{job_id}` | `start_evaluation` | 1067 |
| GET | `/api/evaluation-errors/{job_id}` | `evaluation_errors` | 1075 |
| GET | `/api/workspace` | `workspace` | 1085 |
| PATCH | `/api/workspace` | `save_workspace` | 1092 |
| POST | `/api/ratings` | `add_rating` | 1106 |
| GET | `/api/ratings/{job_id}` | `ratings` | 1121 |
| GET | `/api/agreement` | `agreement` | 1131 |
| POST | `/api/batches` | `batch_experiment` | 1167 |
| GET | `/api/batches/{batch_id}` | `batch_status` | 1247 |
| GET | `/api/exports/{batch_id}` | `export_batch` | 1268 |
| POST | `/api/session` | `login` | 1354 |
| POST | `/api/password-reset` | `reset_password` | 1386 |
| GET | `/api/session` | `session_state` | 1411 |
| DELETE | `/api/session` | `logout` | 1417 |
| GET | `/api/deployment` | `deployment` | 1426 |
| GET | `/api/readiness` | `readiness` | 1432 |
| GET | `/` | `home` | 1539 |

## Compatibility and deliberate changes

Existing routes are preserved. Session POST accepts either `{token}` or `{username,password}` according to AUTH_MODE. Session GET reports auth mode/current authentication. Password-reset POST accepts `{token,password}`. `/api/deployment` is public sanitized identity; `/api/readiness` is private and queries SQLite.

Generation POST accepts nullable `temperature` (0.1–1.5), `speech_rate` (0.8–1.1) and `seed` (0–4294967295). Language is German/English/de/en. Temperature with CosyVoice returns 422; seed requires REPRODUCIBLE=1. Frontend text is capped at the backend limit of 2000 characters. Cache/download behavior uses the submitted version/settings.

Missing credentials no longer permit private requests. Old signed timestamp cookies are not accepted; log in again. Uploads create per-user immutable versions instead of overwriting backend/reference. Workspace/history and private object responses are owner-scoped. This is an intentional security compatibility break.

Legacy benchmark CLI access is local administrative filesystem access and does not go through HTTP authorization. Secure the service account accordingly.

Current additions: generation `model_settings` contains validated optional per-model temperature/rate fields. CosyVoice temperature is rejected. Inputs reject unknown fields and Boolean/numeric coercions. Reference upload accepts `language` and pins transcript versions. `DELETE /api/jobs/{job_id}` cancels only an owned active job and waits for actual termination. `GET /api/admin/security-events` requires administrator authorization. `POST /api/session` accepts `transport: "cookie" | "bearer"`; only explicit bearer transport returns the opaque session token, usable as `Authorization: Session <token>`, with the same expiry/revocation rules.
