"""Process-local deployment admission for the single Uvicorn worker.

The hold shares Orchestrator.lock with Start, Resume, and Retry. It does not
pause, resume, or start a job, and it does not read spreadsheets. Container
/health is intentionally unchanged.

This guard exists on the staging line. The production rollback does not
include it; a host still running that process has no such route.
"""
from __future__ import annotations

import os
from pathlib import Path

SCHEMA = "historyit.deployment-admission.v1"
BOOT_HOLD_ENV = "HISTORYIT_BOOT_DEPLOY_HOLD"
_DEFAULT_STAMP = "/tmp/historyit-boot-hold-consumed"


class DeploymentHoldError(ValueError):
    """New Start/Resume/Retry admission was refused because a deploy hold is active."""


def _stamp_path() -> Path:
    configured = os.environ.get("HISTORYIT_BOOT_HOLD_STAMP", "").strip()
    return Path(configured or _DEFAULT_STAMP)


def apply_boot_hold(orchestrator):
    """Hold a newly created process until the deploy script releases it.

    The Compose override leaves HISTORYIT_BOOT_DEPLOY_HOLD=1 on the container.
    The stamp is written in the container filesystem on the first boot, so a
    later process restart does not apply that same hold again. A new container
    has no stamp and can hold. This does not pause or start a job.
    """
    if os.environ.get(BOOT_HOLD_ENV) != "1":
        return orchestrator
    stamp = _stamp_path()
    if stamp.is_file():
        return orchestrator
    orchestrator._deploy_hold = True
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("consumed\n", encoding="utf-8")
    except OSError:
        # Keep the hold if the stamp cannot be recorded. A restart may then
        # apply it again; release-hold remains the documented recovery.
        pass
    return orchestrator


def _active_jobs(orchestrator):
    active = []
    for job, thread in orchestrator.threads.items():
        alive = getattr(thread, "is_alive", None)
        if callable(alive) and alive():
            active.append(str(job))
    return sorted(active)


def _view(orchestrator):
    active = _active_jobs(orchestrator)
    admitting = orchestrator._admission_depth > 0
    busy = bool(active or admitting)
    return {
        "schema": SCHEMA,
        "state": "active" if busy else "idle",
        "hold": "held" if orchestrator._deploy_hold else "open",
        "active_jobs": active,
        "admitting": admitting,
    }


def status(orchestrator):
    with orchestrator.lock:
        return _view(orchestrator)


def acquire(orchestrator):
    """Hold admission only when no runner is alive and none is being admitted."""
    with orchestrator.lock:
        view = _view(orchestrator)
        if view["state"] != "idle":
            return view
        orchestrator._deploy_hold = True
        return _view(orchestrator)


def release(orchestrator):
    """Clear a hold only while idle. An active runner keeps the hold."""
    with orchestrator.lock:
        view = _view(orchestrator)
        if view["state"] != "idle":
            return view
        orchestrator._deploy_hold = False
        return _view(orchestrator)


def enter_admission(orchestrator):
    with orchestrator.lock:
        if orchestrator._deploy_hold:
            raise DeploymentHoldError("Deployment hold is active; refusing to start a job")
        orchestrator._admission_depth += 1


def leave_admission(orchestrator):
    with orchestrator.lock:
        orchestrator._admission_depth = max(0, orchestrator._admission_depth - 1)
