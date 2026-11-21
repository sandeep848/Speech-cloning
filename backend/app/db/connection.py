"""SQLite unit of work; one connection per operation, rollback on failure."""
import sqlite3
from contextlib import contextmanager

@contextmanager
def connect(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
