"""MongoDB configuration checks.

Startup and health text is chosen from a fixed list so a connection string,
username, or password cannot be reported by accident.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

_LOG = logging.getLogger('historyit.control')

NOT_CONFIGURED = 'MongoDB is not configured'
INVALID = 'MongoDB configuration is invalid'
CONFIGURED = 'MongoDB is configured'
CONFIGURED_FLAGS_OFF = 'MongoDB is configured; control-plane flags are off'
REACHABLE = 'MongoDB is configured and reachable'
UNAVAILABLE = 'MongoDB is configured but unavailable'
PRODUCTION_REFUSED = 'MongoDB control plane is disabled for a production environment label'

_MESSAGES = {
    NOT_CONFIGURED,
    INVALID,
    CONFIGURED,
    CONFIGURED_FLAGS_OFF,
    REACHABLE,
    UNAVAILABLE,
    PRODUCTION_REFUSED,
}

_URI_SHAPE = re.compile(r'^mongodb(?:\+srv)?:\/\/\S+$', re.IGNORECASE)
_SECRET_URI = re.compile(r'mongodb(?:\+srv)?:\/\/\S+', re.IGNORECASE)
_SECRET_PAIR = re.compile(r'(?i)(password|passwd|secret|credential|token|pwd)=([^&\s]+)')
_DB_NAME = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def redact(text: str | None) -> str:
    """Remove connection strings and credential-shaped pairs from a log line."""
    cleaned = _SECRET_URI.sub('[redacted-uri]', str(text or ''))
    cleaned = _SECRET_PAIR.sub(r'\1=[redacted]', cleaned)
    return cleaned[:400]


def uri_is_valid(uri: str | None) -> bool:
    text = str(uri or '').strip()
    if not text or not _URI_SHAPE.match(text):
        return False
    if any(ch in text for ch in ('\n', '\r', ' ', '\x00')):
        return False
    parsed = urlparse(text)
    if parsed.scheme not in ('mongodb', 'mongodb+srv'):
        return False
    if not parsed.hostname:
        return False
    if parsed.username and not parsed.password:
        return False
    return True


def database_name(uri: str, override: str = '') -> str:
    chosen = str(override or '').strip()
    if not chosen and uri_is_valid(uri):
        chosen = (urlparse(uri.strip()).path or '').lstrip('/').split('/')[0]
    if not chosen:
        chosen = 'historyit_control'
    if not _DB_NAME.fullmatch(chosen):
        raise ValueError('invalid')
    return chosen


def flags_enabled(settings) -> bool:
    return bool(
        settings.database_control_plane_enabled
        or settings.database_shadow_writes_enabled
        or settings.database_backfill_enabled
        or getattr(settings, 'database_usage_ingestion_enabled', False)
    )


def production_label(settings) -> bool:
    return str(getattr(settings, 'control_plane_environment', '') or '').strip().lower() == 'production'


def startup_message(settings) -> str:
    """Exact operator-facing sentence. Never the connection string."""
    if production_label(settings):
        return PRODUCTION_REFUSED
    uri = str(getattr(settings, 'mongodb_uri', '') or '').strip()
    if not uri:
        return NOT_CONFIGURED
    if not uri_is_valid(uri):
        return INVALID
    if not flags_enabled(settings):
        return CONFIGURED_FLAGS_OFF
    return CONFIGURED


def announce(settings) -> str:
    message = startup_message(settings)
    if message not in _MESSAGES:
        message = INVALID
    _LOG.info('%s', message)
    for logger_name in ('pymongo', 'pymongo.client', 'pymongo.server'):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    return message


def atlas_host(uri: str) -> bool:
    if not uri_is_valid(uri):
        return False
    host = (urlparse(uri.strip()).hostname or '').lower()
    return host.endswith('.mongodb.net') or 'atlas' in host


def refuse_atlas_during_tests(uri: str) -> None:
    """The test process must not open a production Atlas cluster."""
    if os.environ.get('HISTORYIT_PYTEST') == '1' and atlas_host(uri):
        from app.control_plane.errors import ControlPlaneUnavailable
        raise ControlPlaneUnavailable(UNAVAILABLE)
