"""Publication history persisted in a tracked JSON file (state/state.json).

Why JSON instead of SQLite: the state is committed back to git after each run.
A small text file diffs cleanly, merges with `git pull --rebase`, and can be
inspected on github.com. A binary SQLite file cannot do either.

Safety properties:
* atomic writes (temp file + os.replace) so a crash never leaves half a file;
* strict schema validation on load. Corrupt or unknown state raises StateError,
  and the caller must stop rather than publish blindly;
* an exclusive lock file to prevent two local processes from overlapping
  (CI additionally uses a GitHub Actions concurrency group).
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StateError(RuntimeError):
    """State is missing, unreadable or inconsistent. Publishing must not continue."""


class LockError(RuntimeError):
    """Another run holds the lock."""


def _validate(data: object) -> dict:
    if not isinstance(data, dict):
        raise StateError("state root must be an object")
    if data.get("version") != SCHEMA_VERSION:
        raise StateError(f"unsupported state version: {data.get('version')!r}")
    pubs = data.get("published")
    if not isinstance(pubs, list):
        raise StateError("state.published must be a list")
    seen: set[str] = set()
    for i, rec in enumerate(pubs):
        if not isinstance(rec, dict):
            raise StateError(f"published[{i}] is not an object")
        for key in ("post_id", "date", "media_id", "published_at"):
            if not isinstance(rec.get(key), str) or not rec[key]:
                raise StateError(f"published[{i}].{key} missing or not a string")
        if not _DATE.match(rec["date"]):
            raise StateError(f"published[{i}].date is not YYYY-MM-DD")
        if rec["post_id"] in seen:
            raise StateError(f"post {rec['post_id']} recorded twice")
        seen.add(rec["post_id"])
    return data


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ---------- read ----------
    def load(self) -> dict:
        if not self.path.exists():
            raise StateError(f"state file not found: {self.path}. Refusing to guess. "
                             "Create it with: {\"version\": 1, \"published\": []}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"state file is unreadable/corrupt: {exc}") from exc
        return _validate(data)

    def published_ids(self) -> set[str]:
        return {r["post_id"] for r in self.load()["published"]}

    def published_on(self, date: str) -> dict | None:
        for r in self.load()["published"]:
            if r["date"] == date:
                return r
        return None

    def records_on(self, date: str) -> list[dict]:
        return [r for r in self.load()["published"] if r["date"] == date]

    def history(self) -> list[dict]:
        return list(self.load()["published"])

    # ---------- write ----------
    def _write(self, data: dict) -> None:
        _validate(data)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def record_published(self, *, post_id: str, date: str, media_id: str, category: str,
                         image_url: str, container_id: str | None = None,
                         recovered: bool = False) -> dict:
        """Record a publication Instagram has confirmed. Idempotent for the same post+media."""
        data = self.load()
        for rec in data["published"]:
            if rec["post_id"] == post_id:
                if rec["media_id"] == media_id:
                    return rec  # already recorded (retry), nothing to do
                raise StateError(f"post {post_id} already recorded with a different media id")
        rec = {
            "post_id": post_id,
            "date": date,
            "category": category,
            "media_id": media_id,
            "container_id": container_id,
            "image_url": image_url,
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if recovered:
            rec["recovered"] = True
        data["published"].append(rec)
        self._write(data)
        return rec

    # ---------- locking ----------
    @contextmanager
    def lock(self, stale_after: int = 30 * 60) -> Iterator[None]:
        lock_path = self.path.with_suffix(".lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - lock_path.stat().st_mtime
            if age < stale_after:
                raise LockError(f"another run holds {lock_path.name} ({int(age)}s old)")
            lock_path.unlink(missing_ok=True)  # stale lock from a crashed run
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            lock_path.unlink(missing_ok=True)
