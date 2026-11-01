"""One server process per data directory, with same-process lifespan reentrancy."""

import os
import threading


class DataLock:
    def __init__(self, path):
        self.path = path
        self.guard = threading.Lock()
        self.file = None
        self.references = 0
        self.closing = False

    def acquire(self):
        with self.guard:
            if self.closing:
                raise RuntimeError("Server is still shutting down")
            if self.references:
                self.references += 1
                return False
            file = self.path.open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    if file.seek(0, os.SEEK_END) == 0:
                        file.write(b"0")
                        file.flush()
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                file.close()
                raise RuntimeError(
                    "Another server owns this data directory; use one API worker"
                ) from None
            self.file = file
            self.references = 1
            return True

    def begin_release(self):
        with self.guard:
            self.references -= 1
            if not self.references:
                self.closing = True
                return True
            return False

    def finish_release(self):
        with self.guard:
            self.file.close()
            self.file = None
            self.closing = False
