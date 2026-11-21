"""Persist private execution inputs before admission; additive SQLite migration."""
import time
import wave
from pathlib import Path
from app.services.audio import validate_pcm


def migrate(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS job_inputs (job_id TEXT PRIMARY KEY, model TEXT, text TEXT, path TEXT, transcript TEXT, language TEXT, attempts INTEGER NOT NULL DEFAULT 0)')
    connection.execute('CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)')
    connection.execute('CREATE INDEX IF NOT EXISTS jobs_cache ON jobs(cache_key,status)')
    connection.execute('CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied REAL)')
    connection.execute('INSERT OR IGNORE INTO schema_versions VALUES (1,?)', (time.time(),))


def save(connection, job_id, model, text, path, transcript, language):
    connection.execute('INSERT INTO job_inputs(job_id,model,text,path,transcript,language) VALUES (?,?,?,?,?,?)',
                       (job_id, model, text, path, transcript, language))


def recover(connection, max_attempts, audio_directory=None):
    rows = connection.execute("SELECT jobs.id,job_inputs.* FROM jobs LEFT JOIN job_inputs ON jobs.id=job_inputs.job_id WHERE jobs.status IN ('pending','running') ORDER BY jobs.created").fetchall()
    retry = []
    for row in rows:
        # Publication may have finished immediately before a process crash.
        output = Path(audio_directory) / (row['id'] + '.wav') if audio_directory else None
        if output and output.is_file():
            try:
                validate_pcm(output.read_bytes(), minimum=0.001, maximum=3600)
            except (OSError, ValueError, EOFError, wave.Error):
                pass
            else:
                connection.execute("UPDATE jobs SET status='complete',output=?,error=NULL,updated=? WHERE id=?", (str(output), time.time(), row['id']))
                continue
        if row['job_id'] is not None and row['attempts'] < max_attempts:
            retry.append(row)
            connection.execute("UPDATE jobs SET status='pending',error=NULL,updated=? WHERE id=?", (time.time(), row['id']))
        else:
            connection.execute("UPDATE jobs SET status='failed',error='Interrupted job has no recoverable inputs or retries exhausted',updated=? WHERE id=?", (time.time(), row['id']))
    return retry
