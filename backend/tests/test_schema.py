import sqlite3
import pytest
import schema


def legacy_database(path, orphan=False):
    with sqlite3.connect(path) as c:
        for name,definition in schema.TABLES.items():
            # Representative old schema: same columns but relationships absent.
            import re
            legacy=re.sub(r' REFERENCES \w+\(\w+\)', '', definition)
            c.execute(f'CREATE TABLE {name} ({legacy})')
        c.execute('INSERT INTO references_audio VALUES (?,?,?,?,?)',('ref','hash','ref.wav','/private/ref.wav',1))
        c.execute('INSERT INTO batches VALUES (?,?,?,?)',('batch','text','missing' if orphan else 'ref',1))


def test_migration_preserves_rows_and_enforces_relations(tmp_path):
    path=tmp_path/'legacy.sqlite3';legacy_database(path)
    backup=schema.migrate(path)
    assert backup.is_file()
    with sqlite3.connect(path) as c:
        c.execute('PRAGMA foreign_keys=ON')
        assert not schema.missing_constraints(c)
        assert c.execute('SELECT text FROM batches').fetchone()[0]=='text'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            c.execute('INSERT INTO batches VALUES (?,?,?,?)',('bad','text','absent',1))


def test_bad_legacy_relationships_roll_back(tmp_path):
    path=tmp_path/'legacy.sqlite3';legacy_database(path,orphan=True)
    with pytest.raises(RuntimeError,match='rolled back'):schema.migrate(path)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT reference_id FROM batches').fetchone()[0]=='missing'
        assert schema.missing_constraints(c)
