# API inventory

Authentication is checked by middleware before private endpoints. Foreign private object IDs return 404. Mutating cookie-authenticated requests require `X-Studio-CSRF: 1`.

| Method | Route | Handler | Source line |
|---|---|---|---|
| POST | `/api/admin/reload-reference` | `reload_backend_reference` | 264 |
| GET | `/api/health` | `health` | 308 |
| GET | `/api/models` | `models` | 318 |
| GET | `/api/reference-profile` | `get_reference_profile` | 367 |
| POST | `/api/references/upload` | `upload_reference` | 410 |
| GET | `/api/references/{reference_id}/audio` | `reference_audio` | 453 |
| POST | `/api/generations` | `generate` | 646 |
| GET | `/api/generations/{batch_id}` | `batch` | 824 |
| GET | `/api/recordings/{job_id}/audio` | `recording` | 852 |
| GET | `/api/experiments` | `experiments` | 864 |
| GET | `/api/evaluations/{job_id}` | `evaluations` | 874 |
| POST | `/api/evaluations/{job_id}` | `start_evaluation` | 903 |
| GET | `/api/evaluation-errors/{job_id}` | `evaluation_errors` | 911 |
| GET | `/api/workspace` | `workspace` | 921 |
| PATCH | `/api/workspace` | `save_workspace` | 928 |
| POST | `/api/ratings` | `add_rating` | 942 |
| GET | `/api/ratings/{job_id}` | `ratings` | 957 |
| GET | `/api/agreement` | `agreement` | 967 |
| POST | `/api/batches` | `batch_experiment` | 1003 |
| GET | `/api/batches/{batch_id}` | `batch_status` | 1079 |
| GET | `/api/exports/{batch_id}` | `export_batch` | 1099 |
| POST | `/api/session` | `login` | 1161 |
| POST | `/api/password-reset` | `reset_password` | 1187 |
| GET | `/api/session` | `session_state` | 1209 |
| DELETE | `/api/session` | `logout` | 1215 |
| GET | `/api/deployment` | `deployment` | 1224 |
| GET | `/api/readiness` | `readiness` | 1230 |

## Compatibility and deliberate changes

Existing routes are preserved. Session POST accepts either `{token}` or `{username,password}` according to AUTH_MODE. Session GET reports auth mode/current authentication. Password-reset POST accepts `{token,password}`. `/api/deployment` is public sanitized identity; `/api/readiness` is private and queries SQLite.

Generation POST accepts nullable `temperature` (0.1–1.5), `speech_rate` (0.8–1.1) and `seed` (0–4294967295). Language is German/English/de/en. Temperature with CosyVoice returns 422; seed requires REPRODUCIBLE=1. Frontend text is capped at the backend limit of 2000 characters. Cache/download behavior uses the submitted version/settings.

Missing credentials no longer permit private requests. Old signed timestamp cookies are not accepted; log in again. Uploads create per-user immutable versions instead of overwriting backend/reference. Workspace/history and private object responses are owner-scoped. This is an intentional security compatibility break.

Legacy benchmark CLI access is local administrative filesystem access and does not go through HTTP authorization. Secure the service account accordingly.
