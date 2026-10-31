"""Admin user and workspace membership documents."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROLE_ADMIN: Literal['admin'] = 'admin'
AdminRole = Literal['admin']


class UserRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    username: str
    display_name: str
    password_hash: str
    active: bool = True
    role: AdminRole = ROLE_ADMIN
    created_at: str
    updated_at: str


class MembershipRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    workspace_id: str
    role: AdminRole = ROLE_ADMIN
    created_at: str


class PublicAdmin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    username: str
    display_name: str
    active: bool
    role: AdminRole = ROLE_ADMIN
    created_at: str | None = None
    updated_at: str | None = None


class PublicMembership(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    workspace_id: str
    role: AdminRole = ROLE_ADMIN
    created_at: str | None = None
    username: str | None = None
    display_name: str | None = None


def public_admin(doc: dict) -> dict:
    return PublicAdmin(
        user_id=doc['user_id'],
        username=doc['username'],
        display_name=doc.get('display_name') or doc['username'],
        active=bool(doc.get('active', True)),
        role=ROLE_ADMIN,
        created_at=doc.get('created_at'),
        updated_at=doc.get('updated_at'),
    ).model_dump()


def public_membership(doc: dict, *, user: dict | None = None) -> dict:
    payload = PublicMembership(
        user_id=doc['user_id'],
        workspace_id=doc['workspace_id'],
        role=ROLE_ADMIN,
        created_at=doc.get('created_at'),
        username=(user or {}).get('username'),
        display_name=(user or {}).get('display_name') or (user or {}).get('username'),
    ).model_dump()
    return payload
