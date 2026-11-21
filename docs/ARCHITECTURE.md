# Architecture

One FastAPI application and one Python environment contain Qwen3-TTS, CosyVoice 3, XTTS-v2 and the integrated evaluator. Run exactly one Uvicorn application process. Model threads share a bounded executor and per-model locks; evaluation uses a single bounded admission set. There are no brokers, separate inference services or separately deployed model processes.

| Location | Responsibility |
| --- | --- |
| `backend/backend.py` | Compatibility application, HTTP routes and orchestration; remaining extraction work is tracked in IMPLEMENTATION_STATUS.md |
| `backend/app/config.py` | Fail-closed startup and staging/production configuration |
| `backend/app/schemas/requests.py` | Public request contracts, re-exported by backend.py |
| `backend/app/security/` | Revocable sessions, bounded rate limits and streamed body limits |
| `backend/app/db/connection.py` | Transaction commit/rollback and connection lifetime |
| `backend/app/repositories/jobs.py` | Additive versioned job-input migration and restart reconciliation |
| `backend/app/services/` | PCM validation, atomic file publication and offline backup/restore |
| `backend/models/` | Existing model adapters and pacing; direct_inference.py remains a compatibility entry point |
| `backend/evaluator.py` | Existing integrated automatic metrics |
| `frontend/src/main.tsx` | React bootstrap and error boundary |
| `frontend/src/App.tsx` | Current application composition and state; further component extraction remains |
| `frontend/src/features/` | Generation presets and evaluation definitions |
| `frontend/src/api/` | Central transport and shared types; full OpenAPI type generation remains |

## Admission and recovery

Generation admission is serialized around validation, capacity checks and persistence. Jobs and private execution inputs are committed before tasks are scheduled. An optional `Idempotency-Key` (at most 128 printable ASCII characters) is persisted in the same transaction. Reusing it with changed request fields returns 409; reusing it with the same request returns the original job identifiers.

At startup, pending/running jobs with a valid atomically published final WAV become complete. Other jobs with saved inputs are requeued up to three total execution attempts; legacy jobs without recoverable inputs and exhausted jobs fail explicitly. Complete jobs retain their original output. Transient TimeoutError/ConnectionError failures retry with short bounded delays; invalid inputs, imports, OOM and disk-full errors do not retry automatically.

The process does not execute jobs while stopped. Recovery runs after restart. Pending jobs can be cancelled via `POST /api/generations/{id}/cancel`; native inference already running is allowed to finish. Shutdown drains admitted generation tasks. The service manager must allow sufficient shutdown time for the longest inference; forced termination relies on restart recovery. Evaluation retries remain manually requestable after an interrupted evaluation.

SQLite and audio remain private persistent data. Restores are offline and target a new directory. No retention job deletes private recordings automatically.
