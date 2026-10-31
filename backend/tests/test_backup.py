import io
import sqlite3
import tarfile
import wave
import pytest
from backup import snapshot, restore_archive


def test_snapshot_restore_and_missing_audio(tmp_path):
    data=tmp_path/'source';(data/'audio').mkdir(parents=True)
    audio=data/'audio'/'test.wav'
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(bytes(48000))
    with sqlite3.connect(data/'studio.sqlite3') as c:
        c.executescript('CREATE TABLE references_audio(id TEXT,path TEXT);CREATE TABLE jobs(id TEXT,output TEXT);CREATE TABLE sessions(id TEXT);CREATE TABLE password_resets(id TEXT);')
        c.execute('INSERT INTO references_audio VALUES (?,?)',('reference',str(audio)))
    snapshot(data,tmp_path/'snapshot')
    archive=tmp_path/'backup.tar'
    with tarfile.open(archive,'w') as tar:
        for path in (tmp_path/'snapshot').iterdir():tar.add(path,arcname=path.name)
    restored=tmp_path/'restored'
    restore_archive(archive,restored)
    with sqlite3.connect(restored/'studio.sqlite3') as c:
        assert c.execute('SELECT path FROM references_audio').fetchone()[0] == str(restored/'audio'/'test.wav')
    assert (restored/'audio'/'test.wav').read_bytes()==audio.read_bytes()
    with pytest.raises(RuntimeError):restore_archive(archive,restored)
    audio.unlink()
    with pytest.raises(RuntimeError,match='Missing'):snapshot(data,tmp_path/'missing')


def test_restore_rejects_traversal(tmp_path):
    archive=tmp_path/'bad.tar'
    with tarfile.open(archive,'w') as tar:
        item=tarfile.TarInfo('../escape');item.size=4;tar.addfile(item,io.BytesIO(b'evil'))
    with pytest.raises(RuntimeError,match='Unsafe'):restore_archive(archive,tmp_path/'restore')
    assert not (tmp_path/'escape').exists()
