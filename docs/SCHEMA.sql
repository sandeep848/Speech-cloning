-- Current additive schema; inventory only, not a destructive migration.
CREATE INDEX resource_owner_index ON resource_owners(owner_id, kind);

CREATE INDEX sessions_expiry ON sessions(expires);

CREATE TABLE accounts (
        id TEXT PRIMARY KEY REFERENCES principals(id),
        username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
        credential_version INTEGER NOT NULL DEFAULT 1, disabled INTEGER NOT NULL DEFAULT 0
    );

CREATE TABLE batch_generations (batch_id TEXT, generation_id TEXT, PRIMARY KEY(batch_id,generation_id));

CREATE TABLE batch_runs (id TEXT PRIMARY KEY, params TEXT, created REAL);

CREATE TABLE batches (id TEXT PRIMARY KEY, text TEXT, reference_id TEXT, created REAL);

CREATE TABLE evaluation_errors (job_id TEXT, name TEXT, reason TEXT, PRIMARY KEY(job_id,name));

CREATE TABLE evaluation_runs (job_id TEXT PRIMARY KEY, status TEXT, error TEXT, updated REAL);

CREATE TABLE human_ratings (job_id TEXT, rater TEXT, category TEXT, rating INTEGER, PRIMARY KEY(job_id,rater,category));

CREATE TABLE job_provenance (job_id TEXT PRIMARY KEY REFERENCES jobs(id), configuration TEXT NOT NULL);

CREATE TABLE jobs (id TEXT PRIMARY KEY, batch_id TEXT, model_id TEXT, status TEXT, output TEXT, error TEXT, created REAL, updated REAL, cache_key TEXT, started_at REAL, runtime_seconds REAL);

CREATE TABLE metrics (job_id TEXT, name TEXT, value REAL, evaluator TEXT, PRIMARY KEY(job_id,name));

CREATE TABLE password_resets (
        digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES accounts(id), expires REAL NOT NULL
    );

CREATE TABLE principals (id TEXT PRIMARY KEY);

CREATE TABLE reference_profiles (id INTEGER PRIMARY KEY CHECK(id=1), reference_id TEXT, transcript TEXT NOT NULL DEFAULT '', updated REAL);

CREATE TABLE references_audio (id TEXT PRIMARY KEY, sha256 TEXT, filename TEXT, path TEXT, created REAL);

CREATE TABLE resource_owners (
        kind TEXT NOT NULL, resource_id TEXT NOT NULL,
        owner_id TEXT NOT NULL REFERENCES principals(id),
        PRIMARY KEY(kind, resource_id)
    );

CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied REAL NOT NULL);

CREATE TABLE sessions (
        digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES principals(id),
        expires REAL NOT NULL, credential TEXT NOT NULL
    );

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE user_preferences (
        owner_id TEXT PRIMARY KEY REFERENCES principals(id), value TEXT NOT NULL
    );

CREATE TABLE user_reference_profiles (
        owner_id TEXT NOT NULL REFERENCES principals(id), language TEXT NOT NULL,
        reference_id TEXT NOT NULL REFERENCES references_audio(id),
        transcript TEXT NOT NULL, PRIMARY KEY(owner_id, language)
    );
