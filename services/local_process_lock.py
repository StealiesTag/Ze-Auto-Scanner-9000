"""Small cross-platform file locks used by the one-computer SQLite install."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path


_GUARDS_LOCK = threading.Lock()
_THREAD_GUARDS: dict[str, threading.Lock] = {}


@dataclass
class LocalProcessLock:
    handle: object
    thread_guard: threading.Lock
    windows: bool


def acquire_local_process_lock(path: str | Path) -> LocalProcessLock | None:
    """Acquire a nonblocking thread and OS lock; return None if already held."""
    lock_path = Path(path).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(lock_path))
    with _GUARDS_LOCK:
        guard = _THREAD_GUARDS.setdefault(key, threading.Lock())
    if not guard.acquire(blocking=False):
        return None

    handle = None
    try:
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            windows = True
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            windows = False
        return LocalProcessLock(handle=handle, thread_guard=guard, windows=windows)
    except (OSError, ImportError):
        if handle is not None:
            handle.close()
        guard.release()
        return None


def release_local_process_lock(lock: LocalProcessLock | None) -> None:
    if lock is None:
        return
    try:
        if lock.windows:
            import msvcrt

            lock.handle.seek(0)
            msvcrt.locking(lock.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.handle.close()
        lock.thread_guard.release()


def local_process_lock_is_held(path: str | Path) -> bool:
    lock = acquire_local_process_lock(path)
    if lock is None:
        return True
    release_local_process_lock(lock)
    return False
