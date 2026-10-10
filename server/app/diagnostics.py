"""Safe, local diagnostics for inspection, validation, and benchmark failures.

Diagnostics contain metadata, filenames, counts, and exception chains. They never
contain API keys, prompts, image bytes, or spreadsheet credentials.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        # Keep errors useful while preventing accidental credential leakage.
        value = re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|AIza[A-Za-z0-9_-]+)", "[REDACTED]", value)
        return re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED]", value)
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


def exception_chain(exc: BaseException, limit: int = 8) -> list[dict[str, Any]]:
    """Return the exception and its causes without secrets or request bodies."""
    chain: list[dict[str, Any]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < limit:
        seen.add(id(current))
        item: dict[str, Any] = {
            "type": type(current).__name__,
            "message": _safe(str(current))[:1000],
        }
        status = getattr(current, "status_code", None)
        if status is not None:
            item["status_code"] = status
        code = getattr(current, "code", None)
        if code is not None and not isinstance(code, BaseException):
            item["code"] = _safe(code)
        request = getattr(current, "request", None)
        url = getattr(request, "url", None) if request is not None else None
        if url is not None:
            item["url"] = _safe(str(url).split("?", 1)[0])[:240]
        frames = traceback.extract_tb(current.__traceback__)[-6:]
        if frames:
            item["traceback"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in frames
            ]
        chain.append(item)
        nxt = current.__cause__
        if nxt is None and not current.__suppress_context__:
            nxt = current.__context__
        current = nxt
    return chain


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

    def write_report(self, name: str, **data: Any) -> Path:
        """Write one standalone JSON report, used for checkpoint and comparison runs."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:80] or "report"
        path = self.directory.resolve() / f"{stamp}_{safe_name}.json"
        record = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "report": safe_name,
            **{key: _safe(value) for key, value in data.items()},
        }
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return path
