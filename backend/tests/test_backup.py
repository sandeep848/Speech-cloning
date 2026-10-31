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


def test_age_encrypted_round_trip(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    age=shutil.which('age')
    keygen=shutil.which('age-keygen')
    if not age or not keygen:
        pytest.skip('age and age-keygen required for encrypted recovery integration')
    key=tmp_path/'identity.txt'
    subprocess.run([keygen,'-o',str(key)],check=True,capture_output=True)
    recipient=subprocess.check_output([keygen,'-y',str(key)],text=True).strip()
    data=tmp_path/'source';(data/'audio').mkdir(parents=True)
    with sqlite3.connect(data/'studio.sqlite3') as c:
        c.executescript('CREATE TABLE references_audio(id TEXT,path TEXT);CREATE TABLE jobs(id TEXT,output TEXT);CREATE TABLE sessions(id TEXT);CREATE TABLE password_resets(id TEXT);')
    original=data/'audio'/'encrypted-audio.wav'
    with wave.open(str(original),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(bytes(4800))
    with sqlite3.connect(data/'studio.sqlite3') as c:
        c.execute('INSERT INTO references_audio VALUES (?,?)',('encrypted-reference',str(original)))
    script=Path(__file__).parents[1]/'backup.py'
    encrypted=tmp_path/'encrypted.tar.age'
    subprocess.run([sys.executable,str(script),'backup','--data',str(data),'--archive',str(encrypted),'--recipient',recipient],check=True,capture_output=True)
    assert encrypted.read_bytes().startswith(b'age-encryption.org/')
    restored=tmp_path/'restored'
    subprocess.run([sys.executable,str(script),'restore','--data',str(restored),'--archive',str(encrypted),'--identity',str(key)],check=True,capture_output=True)
    with sqlite3.connect(restored/'studio.sqlite3') as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert (restored/'audio'/'encrypted-audio.wav').read_bytes() == original.read_bytes()
    corrupted=tmp_path/'corrupted.tar.age'
    content=bytearray(encrypted.read_bytes());content[-10]^=1;corrupted.write_bytes(content)
    failed=subprocess.run([sys.executable,str(script),'restore','--data',str(tmp_path/'bad-restore'),'--archive',str(corrupted),'--identity',str(key)],capture_output=True)
    assert failed.returncode != 0
    assert not (tmp_path/'bad-restore').exists()


def test_restore_size_limit_is_checked_before_extraction(tmp_path,monkeypatch):
    archive=tmp_path/'oversized.tar'
    with tarfile.open(archive,'w') as tar:
        member=tarfile.TarInfo('large.bin');member.size=100
        tar.addfile(member,io.BytesIO(bytes(100)))
    monkeypatch.setenv('MAX_RESTORE_BYTES','10')
    root=tmp_path/'new-restore'
    with pytest.raises(RuntimeError,match='limits'):restore_archive(archive,root)
    assert not root.exists()


def test_restore_rejects_incomplete_manifest_and_cleans_partial_directory(tmp_path):
    archive=tmp_path/'incomplete.tar'
    with tarfile.open(archive,'w') as tar:
        member=tarfile.TarInfo('manifest.json');member.size=2
        tar.addfile(member,io.BytesIO(b'{}'))
    root=tmp_path/'new-restore'
    with pytest.raises(RuntimeError,match='manifest'):restore_archive(archive,root)
    assert not root.exists()
