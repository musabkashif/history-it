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


def public_admin(doc: dict) -> dict:
    return PublicAdmin(
        user_id=doc['user_id'],
        username=doc['username'],
        display_name=doc.get('display_name') or doc['username'],
        active=bool(doc.get('active', True)),
        role=ROLE_ADMIN,
    ).model_dump()
