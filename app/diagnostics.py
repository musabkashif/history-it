"""Safe, local diagnostics for inspecting failed inspection attempts.

Diagnostics deliberately contain metadata and filenames only. They never contain
API keys, prompts, image bytes, or spreadsheet credentials.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        # Keep errors useful while preventing accidental credential leakage.
        return re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|AIza[A-Za-z0-9_-]+)", "[REDACTED]", value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    # SDK objects expose useful fields through model_dump/to_dict.
    for method in ("model_dump", "to_dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return _safe(fn())
            except Exception:
                pass
    return str(value)


class Diagnostics:
    def __init__(self, directory: str | os.PathLike[str] = "./logs"):
        self.directory = Path(directory)

    def write(self, job: str, batch: str, attempt: int, event: str, **data: Any) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "job_id": job,
            "batch_id": batch,
            "attempt": attempt,
            "event": event,
            **{key: _safe(value) for key, value in data.items()},
        }
        # One JSON file per attempt is easy to attach and inspect. Rewrite it
        # atomically after every event so a process crash leaves valid JSON.
        path = self.directory.resolve() / f"{job}_{batch}_attempt-{attempt}.json"
        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = []
        existing.append(record)
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(existing, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return path
