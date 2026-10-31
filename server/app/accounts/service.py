"""Admin account bootstrap, password ops, and workspace membership."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import HTTPException

from app.accounts.access import AuthPrincipal, deny_membership, require_admin, stamp_actor
from app.accounts.models import ROLE_ADMIN, public_admin
from app.accounts.store import get_accounts_store
from app import auth

_USERNAME = re.compile(r'^[A-Za-z0-9._@+-]{2,64}$')


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def stable_user_id(username: str) -> str:
    digest = hashlib.sha256(f'admin|{username.strip().casefold()}'.encode('utf-8')).hexdigest()
    return f'usr-{digest[:16]}'


def get_accounts(settings=None) -> 'AccountsService':
    return AccountsService(get_accounts_store(settings), settings=settings)


class AccountsService:
    def __init__(self, store, settings=None):
        self.store = store
        self.settings = settings

    def bootstrap_from_settings(self, settings) -> dict | None:
        """Idempotent Admin seed from APP_LOGIN_USERNAME / APP_LOGIN_PASSWORD_HASH.

        Existing users are left alone. Re-running with the same username keeps the
        same user_id. Password hashes are never returned to API clients.
        Does not implement Operator/Viewer.
        """
        username = (getattr(settings, 'app_login_username', None) or '').strip()
        password_hash = (getattr(settings, 'app_login_password_hash', None) or '').strip()
        if not username or not password_hash:
            return None
        existing = self.store.get_user_by_username(username)
        if existing:
            # Keep hash in sync with env only when the stored hash is empty (should not happen).
            return existing
        now = _now()
        user = {
            'user_id': stable_user_id(username),
            'username': username,
            'display_name': username,
            'password_hash': password_hash,
            'active': True,
            'role': ROLE_ADMIN,
            'created_at': now,
            'updated_at': now,
        }
        return self.store.upsert_user(user)

    def ensure_bootstrap(self, settings) -> dict | None:
        """Seed the env Admin and, on first create only, grant existing workspaces.

        Re-running after the Admin already exists is a no-op for membership so
        later Admins' projects stay isolated. Idempotent upserts still apply if
        assign_existing_workspaces is called explicitly.
        """
        username = (getattr(settings, 'app_login_username', None) or '').strip()
        existed = bool(username and self.store.get_user_by_username(username))
        user = self.bootstrap_from_settings(settings)
        if user and user.get('user_id') and not existed:
            self._assign_all_workspaces(settings, user['user_id'])
        return user

    def _assign_all_workspaces(self, settings, user_id: str) -> int:
        try:
            from app.control_plane.runtime import get_store
            from app.control_plane.errors import ControlPlaneDisabled, ControlPlaneUnavailable
            if not getattr(settings, 'database_control_plane_enabled', False):
                return 0
            store = get_store(settings)
            return self.assign_existing_workspaces(
                user_id,
                [row['workspace_id'] for row in store.list_workspaces()],
            )
        except (ControlPlaneDisabled, ControlPlaneUnavailable, Exception):
            return 0

    def login_configured(self, settings) -> bool:
        try:
            if self.store.list_users():
                return True
        except Exception:
            pass
        return auth.login_configured(settings)

    def authenticate(self, settings, username: str, password: str) -> dict:
        self.ensure_bootstrap(settings)
        user = self.store.get_user_by_username(username.strip())
        if user is None and auth.login_configured(settings):
            # First successful match against env credentials creates the Admin.
            env_user = (settings.app_login_username or '').strip()
            env_hash = (settings.app_login_password_hash or '').strip()
            if (
                env_user
                and env_hash
                and username.strip().casefold() == env_user.casefold()
                and auth.verify_password(env_hash, password)
            ):
                user = self.bootstrap_from_settings(settings)
        if user is None or not auth.verify_password(user.get('password_hash') or '', password):
            raise HTTPException(401, 'Invalid credentials')
        if not user.get('active', True):
            raise HTTPException(403, 'This Admin account is disabled.')
        if user.get('role') != ROLE_ADMIN:
            raise HTTPException(403, 'Admin access required.')
        return user

    def principal_from_user(self, user: dict) -> AuthPrincipal:
        return AuthPrincipal(
            user_id=user['user_id'],
            username=user['username'],
            display_name=user.get('display_name') or user['username'],
            role=ROLE_ADMIN,
            active=bool(user.get('active', True)),
        )

    def get_user(self, user_id: str) -> dict | None:
        return self.store.get_user(user_id)

    def list_admins(self) -> list[dict]:
        return [public_admin(row) for row in self.store.list_users()]

    def change_password(self, principal: AuthPrincipal, current_password: str, new_password: str) -> dict:
        require_admin(principal)
        if principal.is_service or not principal.user_id:
            raise HTTPException(403, 'Admin access required.')
        user = self.store.get_user(principal.user_id)
        if user is None:
            raise HTTPException(404, 'Admin account was not found.')
        if not auth.verify_password(user.get('password_hash') or '', current_password):
            raise HTTPException(401, 'Current password is incorrect.')
        self._validate_password(new_password)
        user['password_hash'] = auth.hash_password(new_password)
        user['updated_at'] = _now()
        return public_admin(self.store.upsert_user(user))

    def reset_password(self, actor: AuthPrincipal, target_user_id: str, new_password: str) -> dict:
        require_admin(actor)
        target = self.store.get_user(target_user_id)
        if target is None:
            raise HTTPException(404, 'Admin account was not found.')
        self._validate_password(new_password)
        target['password_hash'] = auth.hash_password(new_password)
        target['updated_at'] = _now()
        return public_admin(self.store.upsert_user(target))

    def set_active(self, actor: AuthPrincipal, target_user_id: str, active: bool) -> dict:
        require_admin(actor)
        target = self.store.get_user(target_user_id)
        if target is None:
            raise HTTPException(404, 'Admin account was not found.')
        if not active and actor.user_id and actor.user_id == target_user_id:
            raise HTTPException(409, 'You cannot disable your own Admin account while signed in.')
        target['active'] = bool(active)
        target['updated_at'] = _now()
        return public_admin(self.store.upsert_user(target))

    def create_admin(
        self,
        actor: AuthPrincipal,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
    ) -> dict:
        require_admin(actor)
        username = (username or '').strip()
        if not _USERNAME.fullmatch(username):
            raise HTTPException(400, 'Username is invalid.')
        if self.store.get_user_by_username(username):
            raise HTTPException(409, 'An Admin with that username already exists.')
        self._validate_password(password)
        now = _now()
        user = {
            'user_id': stable_user_id(username),
            'username': username,
            'display_name': (display_name or username).strip() or username,
            'password_hash': auth.hash_password(password),
            'active': True,
            'role': ROLE_ADMIN,
            'created_at': now,
            'updated_at': now,
        }
        return public_admin(self.store.upsert_user(user))

    def ensure_membership(self, user_id: str, workspace_id: str) -> dict:
        existing = self.store.get_membership(user_id, workspace_id)
        if existing:
            return existing
        return self.store.upsert_membership({
            'user_id': user_id,
            'workspace_id': workspace_id,
            'role': ROLE_ADMIN,
            'created_at': _now(),
        })

    def assign_existing_workspaces(self, user_id: str, workspace_ids: list[str]) -> int:
        count = 0
        for workspace_id in workspace_ids:
            if not workspace_id:
                continue
            before = self.store.get_membership(user_id, workspace_id)
            self.ensure_membership(user_id, workspace_id)
            if before is None:
                count += 1
        return count

    def is_member(self, user_id: str | None, workspace_id: str | None) -> bool:
        if not user_id or not workspace_id:
            return False
        return self.store.get_membership(user_id, workspace_id) is not None

    def assert_member(self, principal: AuthPrincipal, workspace_id: str | None) -> None:
        if principal.is_service:
            return
        require_admin(principal)
        if not workspace_id or not self.is_member(principal.user_id, workspace_id):
            deny_membership()

    def member_workspace_ids(self, user_id: str) -> set[str]:
        return {row['workspace_id'] for row in self.store.list_memberships(user_id=user_id)}

    def _validate_password(self, password: str) -> None:
        if not password or len(password) < 10:
            raise HTTPException(400, 'Password must be at least 10 characters.')
        if len(password) > 200:
            raise HTTPException(400, 'Password is too long.')


def _job_is_open(job: dict) -> bool:
    status = str(job.get('job_status') or job.get('sheets_status') or '').lower()
    if not status:
        return True
    if status in ('completed', 'validated', 'failed', 'cancelled'):
        return False
    if status in ('queued', 'running', 'paused', 'blocked', 'unknown'):
        return True
    if 'running' in status or 'paused' in status or 'pending' in status or 'authorized' in status:
        return True
    return False


def open_register_conflict(
    *,
    register_id: str | None,
    jobs: list[dict],
    actor_user_id: str | None,
    memberships_by_workspace: dict[str, list[str]],
) -> dict | None:
    """Reject when another Admin already has an open job on this Production Register.

    An open job whose workspace members do not include the actor is always a
    conflict, including unassigned historical workspaces. Historical
    ``created_by`` username strings remain readable on completed jobs; they do
    not grant concurrent register access.
    """
    register = (register_id or '').strip()
    if not register or not actor_user_id:
        return None
    for job in jobs:
        job_register = (job.get('register_id') or '').strip()
        if not job_register or job_register != register:
            continue
        if not _job_is_open(job):
            continue
        workspace_id = job.get('workspace_id')
        members = memberships_by_workspace.get(workspace_id or '', [])
        if actor_user_id in members:
            continue
        return {
            'register_id': register,
            'job_id': job.get('job_id'),
            'workspace_id': workspace_id,
            'created_by': job.get('created_by'),
        }
    return None


# Re-export for callers that import stamp from service historically.
__all__ = [
    'AccountsService',
    'get_accounts',
    'open_register_conflict',
    'stable_user_id',
    'stamp_actor',
]
