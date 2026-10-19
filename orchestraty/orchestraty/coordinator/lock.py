"""The single-writer lock and the heartbeat.

Only the process holding ``coordinator.lock`` runs as the coordinator; the operating system
releases the lock when that process ends, however it ends. The heartbeat file
``coordinator.alive`` is touched every few seconds by a thread of its own, so a long piece of
work never makes a running coordinator look dead; hooks start a new one only when it is stale.
"""
import os
import threading
import time

HEARTBEAT_SECONDS = 2.0

if os.name == "nt":
    import msvcrt

    def _try_lock(fd):
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd):
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class Lock:
    """An exclusive, non-blocking lock on one file."""

    def __init__(self, path):
        self.path = path
        self._fd = None

    def acquire(self):
        """True when this process now holds the lock; False when another one does."""
        if self._fd is not None:
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self):
        if self._fd is None:
            return
        _unlock(self._fd)
        os.close(self._fd)
        self._fd = None

    @property
    def held(self):
        return self._fd is not None


def touch(path):
    """Create `path` if missing and set its modification time to now."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    os.utime(path, None)


class Heartbeat:
    """Touches a file every `interval` seconds from a daemon thread until stopped."""

    def __init__(self, path, interval=HEARTBEAT_SECONDS):
        self.path = path
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        touch(self.path)
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                touch(self.path)
            except OSError:
                pass

    def stop(self, remove=True):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if remove:
            try:
                os.remove(self.path)
            except OSError:
                pass


def is_fresh(path, stale_seconds, now=None):
    """True when `path` was touched less than `stale_seconds` ago."""
    try:
        return (time.time() if now is None else now) - os.stat(path).st_mtime < stale_seconds
    except OSError:
        return False
