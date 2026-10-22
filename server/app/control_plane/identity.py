"""Workspace and event identities. Project names are folded so casing cannot split a project."""
from __future__ import annotations

import hashlib
import uuid


def workspace_id_for(project_name: str | None, environment: str, job_id: str | None = None) -> str:
    project = (project_name or '').strip()
    env = (environment or 'staging').strip() or 'staging'
    if project:
        raw = f'project|{project.casefold()}|{env.casefold()}'
    elif job_id:
        raw = f'job|{job_id}|{env.casefold()}'
    else:
        raise ValueError('workspace needs a project name or job id')
    return 'ws-' + hashlib.sha256(raw.encode()).hexdigest()[:16]


def event_id_for(job_id: str, event_type: str, new_status: str | None, correlation_id: str | None) -> str:
    if correlation_id:
        raw = f'{job_id}|{event_type}|{new_status or ""}|{correlation_id}'
        return 'evt-' + hashlib.sha256(raw.encode()).hexdigest()[:24]
    return 'evt-' + uuid.uuid4().hex
