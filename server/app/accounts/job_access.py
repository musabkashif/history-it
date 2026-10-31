"""Resolve workspace membership for live Sheets job APIs."""
from __future__ import annotations

from fastapi import HTTPException

from app.accounts.access import AuthPrincipal, deny_membership, stamp_actor
from app.accounts.service import get_accounts, open_register_conflict
from app.control_plane.errors import ControlPlaneDisabled, ControlPlaneUnavailable
from app.control_plane.identity import workspace_id_for
from app.control_plane.runtime import get_store

CONTROL_PLANE_REQUIRED = (
    'Admin project isolation requires the database control plane. '
    'Enable DATABASE_CONTROL_PLANE_ENABLED with a configured MongoDB URI.'
)
JOB_WORKSPACE_UNKNOWN = (
    'This job cannot be mapped to a project workspace you belong to. '
    'Open it from Projects, or ask an Admin to add you to the workspace.'
)


def _control_enabled(settings) -> bool:
    return bool(getattr(settings, 'database_control_plane_enabled', False))


def require_control_store(settings):
    if not _control_enabled(settings):
        raise HTTPException(503, CONTROL_PLANE_REQUIRED)
    try:
        return get_store(settings)
    except ControlPlaneDisabled:
        raise HTTPException(503, CONTROL_PLANE_REQUIRED) from None
    except ControlPlaneUnavailable as exc:
        raise HTTPException(503, str(exc) or 'MongoDB is unavailable.') from None
    except Exception:
        raise HTTPException(503, 'MongoDB is unavailable.') from None


def _safe_store(settings):
    if not _control_enabled(settings):
        return None
    try:
        return get_store(settings)
    except (ControlPlaneDisabled, ControlPlaneUnavailable, Exception):
        return None


def workspace_for_job(
    settings,
    job_id: str,
    *,
    project: str | None = None,
    register_id: str | None = None,
) -> str | None:
    store = _safe_store(settings)
    if store is not None:
        job = store.get_job(job_id)
        if job and job.get('workspace_id'):
            return job['workspace_id']
        if register_id:
            for row in store.list_jobs():
                if (row.get('register_id') or '').strip() == register_id.strip() and row.get('workspace_id'):
                    if row.get('job_id') == job_id or not job:
                        return row['workspace_id']
    if project:
        env = getattr(settings, 'control_plane_environment', 'staging') or 'staging'
        return workspace_id_for(project, env, job_id=job_id)
    return None


def assert_job_access(
    settings,
    principal: AuthPrincipal | None,
    job_id: str,
    *,
    project: str | None = None,
    register_id: str | None = None,
) -> str:
    """Require membership before live job mutate/detail.

    Service tokens and patched null principals bypass (automation only).
    Normal Admin sessions fail closed when the job cannot be mapped to a
    workspace they belong to.
    """
    if principal is None or principal.is_service:
        return ''
    store = require_control_store(settings)
    accounts = get_accounts()
    workspace_id = workspace_for_job(settings, job_id, project=project, register_id=register_id)
    job = store.get_job(job_id)
    if job is not None:
        workspace_id = job.get('workspace_id') or workspace_id
    if not workspace_id:
        raise HTTPException(403, JOB_WORKSPACE_UNKNOWN)
    accounts.assert_member(principal, workspace_id)
    return workspace_id


def assert_workspace_access(settings, principal: AuthPrincipal | None, workspace_id: str | None) -> None:
    if principal is None or principal.is_service:
        return
    if not workspace_id:
        deny_membership()
    get_accounts().assert_member(principal, workspace_id)


def ensure_workspace_member_for_create(settings, principal: AuthPrincipal | None, project: str) -> str:
    """Ensure the Admin is a member of the project workspace (creating membership on create)."""
    env = getattr(settings, 'control_plane_environment', 'staging') or 'staging'
    workspace_id = workspace_id_for(project, env)
    if principal is None or principal.is_service or not principal.user_id:
        return workspace_id
    require_control_store(settings)
    get_accounts().ensure_membership(principal.user_id, workspace_id)
    return workspace_id


def check_register_conflict(settings, principal: AuthPrincipal | None, register_id: str | None) -> None:
    if principal is None or principal.is_service or not principal.user_id or not register_id:
        return
    store = require_control_store(settings)
    accounts = get_accounts()
    jobs = store.list_jobs()
    memberships_by_workspace: dict[str, list[str]] = {}
    for row in accounts.store.list_memberships():
        memberships_by_workspace.setdefault(row['workspace_id'], []).append(row['user_id'])
    conflict = open_register_conflict(
        register_id=register_id,
        jobs=jobs,
        actor_user_id=principal.user_id,
        memberships_by_workspace=memberships_by_workspace,
    )
    if conflict:
        raise HTTPException(
            409,
            'Another Admin already has an open job on this Production Register. '
            f"Conflicting job: {conflict.get('job_id') or 'unknown'}.",
        )


def sheets_job_context(live, job_id: str) -> tuple[str | None, str | None]:
    """Read Project and Spreadsheet ID from the Sheets job index when available."""
    try:
        row = live()._locator(job_id)
    except Exception:
        return None, None
    return (row.get('Project') or None), (row.get('Spreadsheet ID') or None)


def actor_stamp(principal: AuthPrincipal | None) -> str | None:
    return stamp_actor(principal)
