"""Persistent spawned workers with bounded execution and confirmed termination.

Only internal, trusted request objects cross private multiprocessing pipes.
No inference result, evaluator score or checkpoint is synthesized here.
"""
from __future__ import annotations
import multiprocessing as mp
import logging
import os
import threading
import time
from pathlib import Path


class WorkerFailure(RuntimeError):
    pass


class TerminationFailure(WorkerFailure):
    pass


class InferenceCancelled(WorkerFailure):
    pass


_evaluator = None


def _perform(kind, payload):
    global _evaluator
    if kind == 'evaluation':
        if _evaluator is None:
            from evaluator import Evaluator
            _evaluator = Evaluator()
        if payload is None:
            return {'ready': not _evaluator.initialization_errors}
        measures = _evaluator.evaluate(*payload)
        return measures, dict(_evaluator.errors)
    from models import get_engine, generate_audio, _request_language, get_timing
    if payload is None:
        get_engine(kind)
        return {'ready': True}
    text, reference, transcript, output, language, config = payload
    from generation_config import current
    language_token = _request_language.set(language)
    config_token = current.set(config)
    started = time.perf_counter()
    try:
        from inference_context import seeded_inference
        with seeded_inference(config.seed):
            generate_audio(kind, text, Path(reference), transcript, Path(output))
        return str(output), time.perf_counter()-started, get_timing(output, 'first_audio_ms')
    finally:
        current.reset(config_token)
        _request_language.reset(language_token)


def _child(connection, kind, handler):
    parent = mp.parent_process()
    def watch_parent():
        while parent is not None and parent.is_alive():
            time.sleep(.25)
        os._exit(70)
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        while True:
            payload = connection.recv()
            try:
                result = handler(kind, payload)
                connection.send({'ok': True, 'result': result})
            except Exception as error:
                logging.getLogger('studio.worker').exception('Worker operation failed kind=%s', kind)
                # Do not serialize exception messages containing private input data.
                connection.send({'ok': False, 'error_type': type(error).__name__})
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()


class Worker:
    def __init__(self, kind, handler=_perform, abort=None):
        self.abort = abort or threading.Event()
        self.kind, self.handler = kind, handler
        self.context = mp.get_context('spawn')
        self.lock = threading.Lock()
        self.process = None
        self.connection = None
        self.state = 'idle'

    def status(self):
        alive = self.process is not None and self.process.is_alive()
        return {'state': self.state if alive else ('failed' if self.state != 'idle' else 'idle'),
                'loaded': alive and self.state == 'ready'}

    def _stop(self):
        process = self.process
        self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            if process.is_alive():
                # A resource slot must never be silently reused after failed reaping.
                self.process = process
                raise TerminationFailure('Worker termination could not be confirmed')
            process.close()

    def close(self):
        with self.lock:
            self._stop()

    def call(self, payload, *, timeout=600, cancelled=None):
        if timeout <= 0:
            raise ValueError('Worker timeout must be positive')
        started = time.monotonic()
        while not self.lock.acquire(timeout=min(.1, timeout)):
            if self.abort.is_set() or (cancelled is not None and cancelled.is_set()):
                raise InferenceCancelled('Job cancelled while waiting for worker')
            if time.monotonic()-started >= timeout:
                raise TimeoutError('Worker remained busy beyond the request limit')
        try:
            if self.process is not None and self.connection is None:
                raise TerminationFailure("Unreaped worker requires operator intervention")
            if self.abort.is_set() or (cancelled is not None and cancelled.is_set()):
                raise InferenceCancelled('Job cancelled before execution')
            if self.process is None or not self.process.is_alive():
                self._stop()
                parent, child = self.context.Pipe()
                self.process = self.context.Process(target=_child, args=(child, self.kind, self.handler), daemon=True)
                self.process.start()
                child.close()
                self.connection = parent
            try:
                started = time.monotonic()
                self.state = 'loading' if payload is None else 'running'
                self.connection.send(payload)
                while True:
                    if self.abort.is_set() or (cancelled is not None and cancelled.is_set()):
                        self._stop()
                        raise InferenceCancelled('Job cancelled')
                    if time.monotonic()-started >= timeout:
                        self._stop()
                        raise TimeoutError('Worker exceeded the configured execution limit')
                    if self.connection.poll(min(.1, timeout)):
                        response = self.connection.recv()
                        if not response['ok']:
                            self.state = 'failed'
                            raise WorkerFailure('Worker failed: '+response['error_type'])
                        self.state = 'ready'
                        return response['result']
                    if not self.process.is_alive():
                        self._stop()
                        raise WorkerFailure('Worker exited unexpectedly')
            except TimeoutError:
                raise
            except (EOFError, BrokenPipeError, OSError):
                self._stop()
                raise WorkerFailure('Worker connection lost') from None

        finally:
            self.lock.release()


class WorkerPool:
    def __init__(self):
        self.workers = {}
        self.abort = threading.Event()
        self.lock = threading.Lock()

    def get(self, kind):
        with self.lock:
            if self.abort.is_set():
                raise InferenceCancelled("Worker pool is shutting down")
            if kind not in self.workers:
                self.workers[kind] = Worker(kind, abort=self.abort)
            return self.workers[kind]

    def status(self, kind):
        with self.lock:
            worker = self.workers.get(kind)
            return worker.status() if worker else {'state': 'idle', 'loaded': False}

    def close(self):
        self.abort.set()
        for worker in list(self.workers.values()):
            worker.close()
