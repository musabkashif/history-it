"""Accounts store selection: Mongo for runtime, memory for isolated unit tests."""
from __future__ import annotations

import copy
import logging
import threading

from app.accounts.mongo_store import ACCOUNT_INDEX_SPECS, MongoAccountsStore

_LOG = logging.getLogger('historyit.accounts')


def _copy(doc: dict | None) -> dict | None:
    return None if doc is None else copy.deepcopy(doc)


class MemoryAccountsStore:
    """In-memory store for isolated unit tests only."""

    def __init__(self):
        self._lock = threading.Lock()
        self.users: dict[str, dict] = {}
        self.users_by_username: dict[str, str] = {}
        self.memberships: dict[tuple[str, str], dict] = {}

    def ensure_indexes(self) -> list[dict]:
        return [dict(item) for item in ACCOUNT_INDEX_SPECS]

    def clear(self) -> None:
        with self._lock:
            self.users.clear()
            self.users_by_username.clear()
            self.memberships.clear()

    def upsert_user(self, doc: dict) -> dict:
        stored = _copy(doc)
        assert stored is not None
        username_key = stored['username'].casefold()
        stored['username_key'] = username_key
        with self._lock:
            existing_id = self.users_by_username.get(username_key)
            if existing_id and existing_id != stored['user_id']:
                raise ValueError(f'Username already registered: {stored["username"]}')
            prior = self.users.get(stored['user_id'])
            if prior and prior.get('username', '').casefold() != username_key:
                self.users_by_username.pop(prior['username'].casefold(), None)
            self.users[stored['user_id']] = stored
            self.users_by_username[username_key] = stored['user_id']
            return _copy(stored)

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            return _copy(self.users.get(user_id))

    def get_user_by_username(self, username: str) -> dict | None:
        with self._lock:
            user_id = self.users_by_username.get((username or '').strip().casefold())
            if not user_id:
                return None
            return _copy(self.users.get(user_id))

    def list_users(self) -> list[dict]:
        with self._lock:
            return [_copy(item) for item in self.users.values()]

    def upsert_membership(self, doc: dict) -> dict:
        stored = _copy(doc)
        assert stored is not None
        key = (stored['user_id'], stored['workspace_id'])
        with self._lock:
            self.memberships[key] = stored
            return _copy(stored)

    def get_membership(self, user_id: str, workspace_id: str) -> dict | None:
        with self._lock:
            return _copy(self.memberships.get((user_id, workspace_id)))

    def list_memberships(self, *, user_id: str | None = None, workspace_id: str | None = None) -> list[dict]:
        with self._lock:
            rows = list(self.memberships.values())
        if user_id:
            rows = [row for row in rows if row.get('user_id') == user_id]
        if workspace_id:
            rows = [row for row in rows if row.get('workspace_id') == workspace_id]
        return [_copy(row) for row in rows]

    def delete_membership(self, user_id: str, workspace_id: str) -> bool:
        with self._lock:
            return self.memberships.pop((user_id, workspace_id), None) is not None


_store = None
_store_lock = threading.Lock()
_settings_ref = None


def set_accounts_store(store) -> None:
    """Inject a store for tests. Pass None to clear the override."""
    global _store
    with _store_lock:
        _store = store


def clear_accounts_store() -> None:
    with _store_lock:
        store = _store
    if store is not None and hasattr(store, 'clear'):
        store.clear()
    set_accounts_store(None)


def get_accounts_store(settings=None):
    """Return the active accounts store.

    Runtime uses MongoAccountsStore on the control-plane database when MongoDB
    is configured. Isolated unit tests inject MemoryAccountsStore via
    set_accounts_store.
    """
    global _store, _settings_ref
    with _store_lock:
        if _store is not None:
            return _store
    if settings is None:
        try:
            from app.config import Settings
            settings = Settings()
        except Exception:
            settings = None
    store = _open_mongo_accounts(settings)
    with _store_lock:
        if _store is not None:
            return _store
        if store is not None:
            _store = store
            _settings_ref = settings
            return _store
        # Last resort for misconfigured local runs: process memory (not production).
        _LOG.warning('Accounts store falling back to in-memory; Admin users will not survive restart.')
        _store = MemoryAccountsStore()
        _store.ensure_indexes()
        return _store


def _open_mongo_accounts(settings):
    if settings is None:
        return None
    uri = str(getattr(settings, 'mongodb_uri', '') or '').strip()
    if not uri:
        return None
    try:
        from app.control_plane.config import (
            production_label,
            refuse_atlas_during_tests,
            uri_is_valid,
            database_name,
        )
        from app.control_plane.errors import ControlPlaneUnavailable
    except Exception:
        return None
    if production_label(settings):
        return None
    if not uri_is_valid(uri):
        return None
    try:
        refuse_atlas_during_tests(uri)
    except Exception:
        return None
    try:
        # Prefer the already-connected control-plane database when available.
        from app.control_plane.runtime import get_store
        from app.control_plane.errors import ControlPlaneDisabled, ControlPlaneUnavailable as CPU
        try:
            control = get_store(settings)
            db = getattr(control, 'db', None)
            if db is not None:
                accounts = MongoAccountsStore(db)
                accounts.ensure_indexes()
                return accounts
        except (ControlPlaneDisabled, CPU, Exception):
            pass
        from pymongo import MongoClient
        name = database_name(uri, getattr(settings, 'mongodb_database', ''))
        client = MongoClient(
            uri,
            serverSelectionTimeoutMS=2000,
            connectTimeoutMS=2000,
            uuidRepresentation='standard',
        )
        client.admin.command('ping')
        accounts = MongoAccountsStore(client[name])
        accounts.ensure_indexes()
        return accounts
    except Exception as exc:
        _LOG.warning('MongoDB accounts store unavailable (%s)', exc.__class__.__name__)
        return None


def reset_accounts_runtime() -> None:
    """Drop the cached runtime store so the next call reconnects (tests/restart)."""
    global _store, _settings_ref
    with _store_lock:
        _store = None
        _settings_ref = None
