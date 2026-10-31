# Architecture

The React frontend calls a single FastAPI process, with three model adapters and a separate single-thread evaluation pool. SQLite persists references, generations, jobs, metrics, evaluations, ratings, workspace preferences and research batches. Audio lives below `VOICE_STUDIO_DATA/audio`, outside the static frontend.

`security.py` owns authentication, opaque session verification, account credentials and ownership helpers. `resource_owners` maps each private reference, generation, job and experiment batch to a principal. Child metrics and ratings are authorized through the owning job. User profiles and preferences have explicit principal foreign keys. All SQLite connections enable foreign keys; the original legacy tables still lack several relational constraints. This is an additive migration, not a complete relational redesign.

`API_TOKEN` represents one shared studio principal. It cannot isolate multiple people who share the token. Select `AUTH_MODE=accounts` and provision distinct local accounts for separate users. Trusted recordings installed by a server administrator are deliberately shared templates; uploads never replace them. Existing private records have no inferable owner and remain quarantined until an offline administrator assigns them.

Generations pin a reference ID before submission. Uploaded versions are immutable UUID-named WAV files. A frozen request configuration travels to the inference worker through ContextVar state. Temperature is supported by the existing Qwen and XTTS adapter contracts; CosyVoice temperature is rejected. All three support rate control through their existing processing paths.

Admission is serialized within one worker and checked in an immediate SQLite transaction against pending/running rows and per-user limits. Inference uses threads; strong cancellation, wall-clock termination and worker-process isolation are not implemented. Run exactly one API worker and one service against a data directory. See KNOWN_LIMITATIONS.md before any public use.

Current execution uses persistent supervised subprocesses per model and one evaluator subprocess, with worker RPC carried by bounded parent executor threads. Production rejects thread-only execution. Store locking prevents concurrent server instances; SQLite reservations and durable limits enforce capacity. See HARDENING_FOLLOWUP.md for the current schema, cross-site session transport and migration requirements.
