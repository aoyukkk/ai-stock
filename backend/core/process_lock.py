from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class SingleInstanceError(RuntimeError):
    pass


class ProcessFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, stream)
                self._owned = True
                return
            except FileExistsError:
                if not self._stale():
                    raise SingleInstanceError("INTERNAL_WEB_SERVER_ALREADY_RUNNING")
                self.path.unlink(missing_ok=True)
        raise SingleInstanceError("INTERNAL_WEB_SERVER_LOCK_FAILED")

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def _stale(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            if pid == os.getpid():
                return False
            os.kill(pid, 0)
            return False
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return True

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
