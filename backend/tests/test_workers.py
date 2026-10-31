"""Actual spawned subprocesses; synthetic handlers are isolated test doubles."""
import os
import threading
import time
import pytest
from isolated_workers import Worker, InferenceCancelled, WorkerFailure


def handler(kind, payload):
    if payload == 'hang':
        while True:time.sleep(.1)
    if payload == 'exit':os._exit(7)
    return os.getpid(), payload


def test_timeout_reaps_worker_and_next_request_recovers():
    worker=Worker('test',handler)
    try:
        first,_=worker.call('first',timeout=5)
        assert first != os.getpid()
        with pytest.raises(TimeoutError):worker.call('hang',timeout=.3)
        assert worker.process is None
        second,result=worker.call('recovered',timeout=5)
        assert result=='recovered' and second != first
    finally:worker.close()


def test_cancellation_stops_actual_process():
    worker=Worker('test',handler)
    event=threading.Event()
    try:
        worker.call('warm',timeout=5)
        timer=threading.Timer(.2,event.set);timer.start()
        with pytest.raises(InferenceCancelled):worker.call('hang',timeout=10,cancelled=event)
        timer.join()
        assert worker.process is None
    finally:worker.close()


def test_crashed_worker_recovers():
    worker=Worker('test',handler)
    try:
        with pytest.raises(WorkerFailure):worker.call('exit',timeout=5)
        assert worker.call('recovered',timeout=5)[1]=='recovered'
    finally:worker.close()


def rpc_audio(kind, payload):
    from pathlib import Path
    import wave
    text,reference,transcript,path,language,config=payload
    if text=='hang':
        while True:time.sleep(.1)
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(bytes(4800))
    return str(path),.1,20


def test_data_directory_lock_stays_held_through_shutdown(tmp_path):
    from runtime_lock import DataLock
    first=DataLock(tmp_path/'runtime.lock');second=DataLock(tmp_path/'runtime.lock')
    assert first.acquire() is True
    assert first.acquire() is False
    assert first.begin_release() is False
    with pytest.raises(RuntimeError,match='Another server'):second.acquire()
    assert first.begin_release() is True
    with pytest.raises(RuntimeError,match='Another server'):second.acquire()
    first.finish_release()
    assert second.acquire() is True
    assert second.begin_release() is True
    second.finish_release()
