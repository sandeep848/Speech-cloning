"""Core relational schema and explicit, backed-up legacy migration."""
from __future__ import annotations
import argparse
from pathlib import Path
import sqlite3
import time

TABLES = {
 'references_audio': 'id TEXT PRIMARY KEY NOT NULL, sha256 TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL, created REAL NOT NULL',
 'batches': 'id TEXT PRIMARY KEY NOT NULL, text TEXT NOT NULL, reference_id TEXT NOT NULL REFERENCES references_audio(id), created REAL NOT NULL',
 'jobs': "id TEXT PRIMARY KEY NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(id), model_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed','cancelled')), output TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL, cache_key TEXT, started_at REAL, runtime_seconds REAL",
 'metrics': 'job_id TEXT NOT NULL REFERENCES jobs(id), name TEXT NOT NULL, value REAL NOT NULL, evaluator TEXT NOT NULL, PRIMARY KEY(job_id,name)',
 'evaluation_runs': 'job_id TEXT PRIMARY KEY NOT NULL REFERENCES jobs(id), status TEXT NOT NULL, error TEXT, updated REAL NOT NULL',
 'reference_profiles': "id INTEGER PRIMARY KEY CHECK(id=1), reference_id TEXT NOT NULL REFERENCES references_audio(id), transcript TEXT NOT NULL DEFAULT '', updated REAL NOT NULL",
 'settings': 'key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL',
 'human_ratings': 'job_id TEXT NOT NULL REFERENCES jobs(id), rater TEXT NOT NULL, category TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), PRIMARY KEY(job_id,rater,category)',
 'evaluation_errors': 'job_id TEXT NOT NULL REFERENCES jobs(id), name TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(job_id,name)',
 'batch_runs': "id TEXT PRIMARY KEY NOT NULL, params TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('planning','ready','interrupted'))",
 'batch_generations': 'batch_id TEXT NOT NULL REFERENCES batch_runs(id), generation_id TEXT NOT NULL REFERENCES batches(id), PRIMARY KEY(batch_id,generation_id)',
}
COPY_STATEMENTS = {
 'references_audio': 'INSERT INTO migration_references_audio (id,sha256,filename,path,created) SELECT id,sha256,filename,path,created FROM references_audio',
 'batches': 'INSERT INTO migration_batches (id,text,reference_id,created) SELECT id,text,reference_id,created FROM batches',
 'jobs': 'INSERT INTO migration_jobs (id,batch_id,model_id,status,output,error,created,updated,cache_key,started_at,runtime_seconds) SELECT id,batch_id,model_id,status,output,error,created,updated,cache_key,started_at,runtime_seconds FROM jobs',
 'metrics': 'INSERT INTO migration_metrics (job_id,name,value,evaluator) SELECT job_id,name,value,evaluator FROM metrics',
 'evaluation_runs': 'INSERT INTO migration_evaluation_runs (job_id,status,error,updated) SELECT job_id,status,error,updated FROM evaluation_runs',
 'reference_profiles': 'INSERT INTO migration_reference_profiles (id,reference_id,transcript,updated) SELECT id,reference_id,transcript,updated FROM reference_profiles',
 'settings': 'INSERT INTO migration_settings (key,value) SELECT key,value FROM settings',
 'human_ratings': 'INSERT INTO migration_human_ratings (job_id,rater,category,rating) SELECT job_id,rater,category,rating FROM human_ratings',
 'evaluation_errors': 'INSERT INTO migration_evaluation_errors (job_id,name,reason) SELECT job_id,name,reason FROM evaluation_errors',
 'batch_runs': 'INSERT INTO migration_batch_runs (id,params,created,status) SELECT id,params,created,status FROM batch_runs',
 'batch_generations': 'INSERT INTO migration_batch_generations (batch_id,generation_id) SELECT batch_id,generation_id FROM batch_generations',
}

INDEXES = (
 'CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id)',
 'CREATE INDEX IF NOT EXISTS jobs_cache ON jobs(cache_key, status)',
 'CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)',
 'CREATE INDEX IF NOT EXISTS batches_reference ON batches(reference_id)',
 'CREATE INDEX IF NOT EXISTS batch_generation_child ON batch_generations(generation_id)',
)


def initialize(connection):
    for name, definition in TABLES.items():
        connection.execute(f'CREATE TABLE IF NOT EXISTS {name} ({definition})')
    # Safe additive compatibility updates; relational rebuilding is explicit below.
    for name in ('started_at','runtime_seconds'):
        if name not in {r[1] for r in connection.execute('PRAGMA table_info(jobs)')}:
            connection.execute(f'ALTER TABLE jobs ADD COLUMN {name} REAL')
    if 'status' not in {r[1] for r in connection.execute('PRAGMA table_info(batch_runs)')}:
        connection.execute("ALTER TABLE batch_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'")
    for statement in INDEXES:
        connection.execute(statement)


def missing_constraints(connection):
    return [name for name, definition in TABLES.items()
            if definition.count('REFERENCES') != len(connection.execute(f'PRAGMA foreign_key_list({name})').fetchall())]


def _migrate_unlocked(path: Path):
    if not path.is_file():
        raise RuntimeError('Database does not exist')
    backup = path.with_name(path.name+f'.before-schema-{time.time_ns()}.bak')
    # Copy the original consistently before any migration work; never overwrite it.
    backup.touch(mode=0o600, exist_ok=False)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    with sqlite3.connect(path) as c:
        c.execute('PRAGMA foreign_keys=OFF')
        c.execute('BEGIN EXCLUSIVE')
        try:
            initialize(c)
            for name, definition in TABLES.items():
                c.execute(f'CREATE TABLE migration_{name} ({definition})')
                existing = [r[1] for r in c.execute(f'PRAGMA table_info({name})')]
                target_columns = {r[1] for r in c.execute(f'PRAGMA table_info(migration_{name})')}
                if set(existing) - target_columns:
                    raise RuntimeError('Unknown columns: refusing a potentially lossy migration')
                c.execute(COPY_STATEMENTS[name])
            for name in reversed(TABLES):
                c.execute(f'DROP TABLE {name}')
            for name in TABLES:
                c.execute(f'ALTER TABLE migration_{name} RENAME TO {name}')
            for statement in INDEXES:
                c.execute(statement)
            if c.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('Legacy records violate relationships; migration rolled back')
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database integrity validation failed')
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
                c.execute('INSERT OR IGNORE INTO schema_migrations VALUES (2,?)',(time.time(),))
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.execute('PRAGMA foreign_keys=ON')
    return backup


def migrate(path: Path):
    from runtime_lock import DataLock
    path=path.resolve()
    if not path.is_file():
        raise RuntimeError('Database does not exist')
    lock=DataLock(path.parent/'runtime.lock')
    lock.acquire()
    try:
        return _migrate_unlocked(path)
    finally:
        if lock.begin_release():
            lock.finish_release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Explicitly migrate with a pre-migration snapshot')
    args = parser.parse_args()
    if args.apply:
        print('Migration complete. Private pre-migration snapshot:', migrate(args.database))
    else:
        with sqlite3.connect(f'file:{args.database.resolve()}?mode=ro', uri=True) as c:
            missing = missing_constraints(c)
            print('Tables requiring migration:', ', '.join(missing) if missing else 'none')
