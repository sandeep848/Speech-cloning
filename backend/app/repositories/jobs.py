"""Persist private execution inputs before admission; additive SQLite migration."""
import time


def migrate(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS job_inputs (job_id TEXT PRIMARY KEY, model TEXT, text TEXT, path TEXT, transcript TEXT, language TEXT, attempts INTEGER NOT NULL DEFAULT 0)')
    connection.execute('CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)')
    connection.execute('CREATE INDEX IF NOT EXISTS jobs_cache ON jobs(cache_key,status)')
    connection.execute('CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied REAL)')
    connection.execute('INSERT OR IGNORE INTO schema_versions VALUES (1,?)', (time.time(),))


def save(connection, job_id, model, text, path, transcript, language):
    connection.execute('INSERT INTO job_inputs(job_id,model,text,path,transcript,language) VALUES (?,?,?,?,?,?)',
                       (job_id, model, text, path, transcript, language))


def recover(connection, max_attempts):
    rows = connection.execute("SELECT jobs.id,job_inputs.* FROM jobs LEFT JOIN job_inputs ON jobs.id=job_inputs.job_id WHERE jobs.status IN ('pending','running') ORDER BY jobs.created").fetchall()
    retry = []
    for row in rows:
        if row['job_id'] is not None and row['attempts'] < max_attempts:
            retry.append(row)
            connection.execute("UPDATE jobs SET status='pending',error=NULL,updated=? WHERE id=?", (time.time(), row['id']))
        else:
            connection.execute("UPDATE jobs SET status='failed',error='Interrupted job has no recoverable inputs or retries exhausted',updated=? WHERE id=?", (time.time(), row['id']))
    return retry
