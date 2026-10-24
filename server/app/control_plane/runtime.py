"""Lazy MongoDB connection.

The production Atlas cluster is refused while HISTORYIT_PYTEST=1. Connection
failures become a fixed unavailable message.
"""
from __future__ import annotations

import logging
import threading
import time

from app.control_plane.config import (
    INVALID,
    NOT_CONFIGURED,
    PRODUCTION_REFUSED,
    REACHABLE,
    UNAVAILABLE,
    database_name,
    flags_enabled,
    production_label,
    redact,
    refuse_atlas_during_tests,
    startup_message,
    uri_is_valid,
)
from app.control_plane.errors import ControlPlaneDisabled, ControlPlaneUnavailable

_LOG = logging.getLogger('historyit.control')
_LOCK = threading.Lock()
_override = None
_live_store = None
_probe = {'at': 0.0, 'available': None}


def set_store(store) -> None:
    global _override
    _override = store


def clear_store() -> None:
    global _override, _live_store
    _override = None
    _live_store = None


def get_store(settings):
    if _override is not None:
        return _override
    if production_label(settings):
        raise ControlPlaneUnavailable(PRODUCTION_REFUSED)
    if not flags_enabled(settings):
        raise ControlPlaneDisabled(NOT_CONFIGURED)
    uri = str(getattr(settings, 'mongodb_uri', '') or '').strip()
    if not uri:
        raise ControlPlaneUnavailable(NOT_CONFIGURED)
    if not uri_is_valid(uri):
        raise ControlPlaneUnavailable(INVALID)
    refuse_atlas_during_tests(uri)
    return _connect(settings, uri)


def _connect(settings, uri: str):
    global _live_store
    with _LOCK:
        if _live_store is not None:
            return _live_store
        refuse_atlas_during_tests(uri)
        try:
            name = database_name(uri, getattr(settings, 'mongodb_database', ''))
        except ValueError:
            raise ControlPlaneUnavailable(INVALID) from None
        try:
            from pymongo import MongoClient
            from app.control_plane.mongo import MongoStore
            client = MongoClient(
                uri,
                serverSelectionTimeoutMS=2000,
                connectTimeoutMS=2000,
                uuidRepresentation='standard',
            )
            client.admin.command('ping')
            store = MongoStore(client[name])
            store.ensure_indexes()
        except ControlPlaneUnavailable:
            raise
        except Exception as exc:
            _LOG.warning('MongoDB is configured but unavailable (%s)', redact(str(exc)))
            raise ControlPlaneUnavailable(UNAVAILABLE) from None
        _live_store = store
        return store


def health_fields(settings) -> dict:
    message = startup_message(settings)
    uri = str(getattr(settings, 'mongodb_uri', '') or '').strip()
    configured = bool(uri) and uri_is_valid(uri) and not production_label(settings)
    available = None
    if production_label(settings):
        available = False
        message = PRODUCTION_REFUSED
    elif uri and not uri_is_valid(uri):
        configured = False
        message = INVALID
    elif flags_enabled(settings) and configured:
        available = cached_probe(settings)
        message = REACHABLE if available else UNAVAILABLE
    return {
        'database_configured': configured,
        'database_available': available,
        'database_control_plane_enabled': bool(settings.database_control_plane_enabled),
        'database_shadow_writes_enabled': bool(settings.database_shadow_writes_enabled),
        'database_backfill_enabled': bool(settings.database_backfill_enabled),
        'database_usage_ingestion_enabled': bool(getattr(settings, 'database_usage_ingestion_enabled', False)),
        'database_message': message,
    }


def cached_probe(settings) -> bool:
    now = time.monotonic()
    if _probe['available'] is not None and now - _probe['at'] < 15:
        return bool(_probe['available'])
    try:
        get_store(settings)
        available = True
    except Exception as exc:
        _LOG.warning('MongoDB is configured but unavailable (%s)', redact(str(exc)))
        available = False
    _probe['at'] = now
    _probe['available'] = available
    return available
