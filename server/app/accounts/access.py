"""Auth principals and Admin-only helpers."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.accounts.models import ROLE_ADMIN


@dataclass(frozen=True)
class AuthPrincipal:
    """Authenticated caller. Service tokens have role='service' and no user_id."""

    username: str
    role: str
    user_id: str | None = None
    display_name: str = ''
    active: bool = True

    @property
    def is_service(self) -> bool:
        return self.role == 'service'

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN and bool(self.user_id) and self.active


def stamp_actor(principal: AuthPrincipal | None) -> str | None:
    """Stable identity for new created_by / event actor fields.

    Historical shared-username strings remain readable as-is. New stamps use
    user_id when available so concurrent Admins do not collide on username.
    """
    if principal is None:
        return None
    if principal.is_service:
        return 'service'
    if principal.user_id:
        return principal.user_id
    return principal.username or None


def require_admin(principal: AuthPrincipal) -> AuthPrincipal:
    """Every signed-in operator is Admin. Operator/Viewer are not implemented."""
    if principal.is_service:
        return principal
    if not principal.is_admin:
        raise HTTPException(403, 'Admin access required.')
    return principal


MEMBERSHIP_DENIED = 'You are not a member of this project workspace.'


def deny_membership() -> None:
    raise HTTPException(403, MEMBERSHIP_DENIED)
