-- Current schema; inventory only. Use schema.py for backed-up legacy migrations.
CREATE INDEX artifact_sizes_owner ON artifact_sizes(owner_id);

CREATE INDEX batch_generation_child ON batch_generations(generation_id);

CREATE INDEX batches_reference ON batches(reference_id);

CREATE INDEX jobs_batch ON jobs(batch_id);

CREATE INDEX jobs_cache ON jobs(cache_key, status);

CREATE INDEX jobs_status ON jobs(status);

CREATE INDEX rate_limits_expiry ON rate_limits(expires);

CREATE INDEX reservations_owner ON storage_reservations(owner_id);

CREATE INDEX resource_owner_index ON resource_owners(owner_id, kind);

CREATE INDEX sessions_expiry ON sessions(expires);

CREATE TABLE accounts (
        id TEXT PRIMARY KEY REFERENCES principals(id),
        username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
        credential_version INTEGER NOT NULL DEFAULT 1, disabled INTEGER NOT NULL DEFAULT 0
    );

CREATE TABLE artifact_sizes (
        path TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        size INTEGER NOT NULL CHECK(size>=0), kind TEXT NOT NULL
    );

CREATE TABLE batch_generations (batch_id TEXT NOT NULL REFERENCES batch_runs(id), generation_id TEXT NOT NULL REFERENCES batches(id), PRIMARY KEY(batch_id,generation_id));

CREATE TABLE batch_runs (id TEXT PRIMARY KEY NOT NULL, params TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('planning','ready','interrupted')));

CREATE TABLE batches (id TEXT PRIMARY KEY NOT NULL, text TEXT NOT NULL, reference_id TEXT NOT NULL REFERENCES references_audio(id), created REAL NOT NULL);

CREATE TABLE evaluation_errors (job_id TEXT NOT NULL REFERENCES jobs(id), name TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(job_id,name));

CREATE TABLE evaluation_runs (job_id TEXT PRIMARY KEY NOT NULL REFERENCES jobs(id), status TEXT NOT NULL, error TEXT, updated REAL NOT NULL);

CREATE TABLE human_ratings (job_id TEXT NOT NULL REFERENCES jobs(id), rater TEXT NOT NULL, category TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), PRIMARY KEY(job_id,rater,category));

CREATE TABLE job_provenance (job_id TEXT PRIMARY KEY REFERENCES jobs(id), configuration TEXT NOT NULL);

CREATE TABLE jobs (id TEXT PRIMARY KEY NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(id), model_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','cancelled')), output TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL, cache_key TEXT, started_at REAL, runtime_seconds REAL);

CREATE TABLE metrics (job_id TEXT NOT NULL REFERENCES jobs(id), name TEXT NOT NULL, value REAL NOT NULL, evaluator TEXT NOT NULL, PRIMARY KEY(job_id,name));

CREATE TABLE password_resets (
        digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES accounts(id), expires REAL NOT NULL
    );

CREATE TABLE principals (id TEXT PRIMARY KEY);

CREATE TABLE rate_limits (
        key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
    );

CREATE TABLE reference_profiles (id INTEGER PRIMARY KEY CHECK(id=1), reference_id TEXT NOT NULL REFERENCES references_audio(id), transcript TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);

CREATE TABLE reference_versions (reference_id TEXT PRIMARY KEY REFERENCES references_audio(id), transcript TEXT NOT NULL, language TEXT NOT NULL);

CREATE TABLE references_audio (id TEXT PRIMARY KEY NOT NULL, sha256 TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL, created REAL NOT NULL);

CREATE TABLE resource_owners (
        kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        owner_id TEXT NOT NULL REFERENCES principals(id),
        PRIMARY KEY(kind, resource_id)
    );

CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied REAL NOT NULL);

CREATE TABLE security_events (
        id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL,
        request_id TEXT NOT NULL
    );

CREATE TABLE sessions (
        digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        expires REAL NOT NULL, credential TEXT NOT NULL
    );

CREATE TABLE settings (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);

CREATE TABLE storage_reservations (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        size INTEGER NOT NULL CHECK(size>=0)
    );

CREATE TABLE user_preferences (
        owner_id TEXT PRIMARY KEY REFERENCES principals(id), value TEXT NOT NULL
    );

CREATE TABLE user_reference_profiles (
        owner_id TEXT NOT NULL REFERENCES principals(id), language TEXT NOT NULL,
        reference_id TEXT NOT NULL REFERENCES references_audio(id),
        transcript TEXT NOT NULL, PRIMARY KEY(owner_id, language)
    );
