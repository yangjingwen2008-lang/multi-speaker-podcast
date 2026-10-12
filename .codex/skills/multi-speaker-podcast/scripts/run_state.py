"""Kernel-backed output locking and atomic, credential-free run journals."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ConcurrentRunError(RuntimeError):
    pass


class OutputLock:
    def __init__(self, directory: Path):
        self.path = directory / ".pipeline.lock"
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise ConcurrentRunError("Output directory is locked by another podcast run.") from exc
        return self

    def __exit__(self, *_):
        if self.handle:
            self.handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()


def write_json(path: Path, data: dict):
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        for attempt in range(7):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(0.025 * 2**attempt)
    finally:
        temporary.unlink(missing_ok=True)


def begin(directory: Path, overwrite: bool) -> str:
    run_id = uuid.uuid4().hex
    if overwrite:
        records = [
            "run_state.json",
            "production_status.json",
            "release_manifest.json",
            "audition_package/audition_manifest.json",
            "audition_package/session_report.json",
        ]
        for name in records:
            source = directory / name
            if source.is_file():
                target = directory / "run_history" / run_id / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    write_json(
        directory / "run_state.json",
        {
            "run_id": run_id,
            "status": "running",
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    write_json(directory / "production_status.json", {"run_id": run_id, "status": "running"})
    write_json(directory / "release_manifest.json", {"run_id": run_id, "status": "running", "files": []})
    package = directory / "audition_package"
    package.mkdir(exist_ok=True)
    write_json(package / "audition_manifest.json", {"run_id": run_id, "status": "running"})
    return run_id


def finish(directory: Path, run_id: str, exit_code: int):
    status = "completed" if exit_code == 0 else "technical_qa_failed"
    for name in ("production_status.json", "release_manifest.json", "audition_package/audition_manifest.json"):
        path = directory / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["run_id"] = run_id
        if name != "production_status.json":
            data["status"] = status
        write_json(path, data)
    state = json.loads((directory / "run_state.json").read_text(encoding="utf-8"))
    state.update(status=status, finished_at=datetime.now(timezone.utc).isoformat(), exit_code=exit_code)
    write_json(directory / "run_state.json", state)


def fail(directory: Path, run_id: str, message: str):
    for name in ("production_status.json", "release_manifest.json", "audition_package/audition_manifest.json"):
        write_json(directory / name, {"run_id": run_id, "status": "failed", "files": [], "error": message})
    write_json(
        directory / "run_state.json",
        {"run_id": run_id, "status": "failed", "exit_code": 1, "finished_at": datetime.now(timezone.utc).isoformat()},
    )
    (directory / "failure_report.md").write_text(f"# Pipeline failed\n\n{message}\n", encoding="utf-8")
