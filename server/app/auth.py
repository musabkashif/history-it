"""Cookie sessions, Argon2 login, CSRF, and login throttling."""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request, Response

SESSION_COOKIE = 'historyit_session'
CSRF_COOKIE = 'historyit_csrf'
CSRF_HEADER = 'X-CSRF-Token'

_hasher = PasswordHasher()


@dataclass
class SessionRecord:
    username: str
    csrf_token: str
    expires_at: float


class SessionStore:
    """Thread-safe in-memory sessions for the single Uvicorn worker."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, username: str, ttl_seconds: int) -> tuple[str, SessionRecord]:
        token = secrets.token_urlsafe(32)
        record = SessionRecord(
            username=username,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=time.time() + max(60, ttl_seconds),
        )
        with self._lock:
            self._purge_locked()
            self._sessions[token] = record
        return token, record

    def get(self, token: str | None) -> SessionRecord | None:
        if not token:
            return None
        with self._lock:
            self._purge_locked()
            record = self._sessions.get(token)
            if not record:
                return None
            if record.expires_at <= time.time():
                self._sessions.pop(token, None)
                return None
            return record

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)


class LoginThrottle:
    """Fail closed after too many failed attempts per client key."""

    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()

    def assert_allowed(self, key: str, max_failures: int, window_seconds: int, lockout_seconds: int) -> None:
        now = time.time()
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until > now:
                raise HTTPException(429, 'Too many login attempts. Try again later.')
            cutoff = now - max(1, window_seconds)
            recent = [stamp for stamp in self._failures.get(key, []) if stamp >= cutoff]
            self._failures[key] = recent
            if len(recent) >= max(1, max_failures):
                self._locked_until[key] = now + max(1, lockout_seconds)
                self._failures[key] = []
                raise HTTPException(429, 'Too many login attempts. Try again later.')

    def record_failure(self, key: str, max_failures: int, window_seconds: int, lockout_seconds: int) -> None:
        now = time.time()
        with self._lock:
            cutoff = now - max(1, window_seconds)
            recent = [stamp for stamp in self._failures.get(key, []) if stamp >= cutoff]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= max(1, max_failures):
                self._locked_until[key] = now + max(1, lockout_seconds)
                self._failures[key] = []

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


sessions = SessionStore()
throttle = LoginThrottle()


def login_configured(settings) -> bool:
    username = (settings.app_login_username or '').strip()
    password_hash = (settings.app_login_password_hash or '').strip()
    return bool(username and password_hash)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def client_key(request: Request) -> str:
    forwarded = (request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return 'unknown'


def allowed_origins(settings) -> list[str]:
    return [item.strip().rstrip('/') for item in (settings.allowed_origins or '').split(',') if item.strip()]


def origin_allowed(request: Request, settings) -> bool:
    allowed = allowed_origins(settings)
    if not allowed:
        return False
    origin = (request.headers.get('origin') or '').rstrip('/')
    if origin and origin in allowed:
        return True
    referer = request.headers.get('referer') or ''
    for item in allowed:
        if referer.startswith(item + '/') or referer == item:
            return True
    return False


def cookie_secure(settings, request: Request) -> bool:
    mode = (settings.cookie_secure or 'auto').strip().lower()
    if mode in ('1', 'true', 'yes', 'on'):
        return True
    if mode in ('0', 'false', 'no', 'off'):
        return False
    proto = (request.headers.get('x-forwarded-proto') or request.url.scheme or '').lower()
    return proto == 'https'


def set_auth_cookies(response: Response, request: Request, settings, session_token: str, csrf_token: str) -> None:
    secure = cookie_secure(settings, request)
    common = {
        'httponly': True,
        'secure': secure,
        'samesite': settings.cookie_samesite,
        'path': '/',
        'max_age': settings.session_ttl_seconds,
    }
    response.set_cookie(SESSION_COOKIE, session_token, **common)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=secure,
        samesite=settings.cookie_samesite,
        path='/',
        max_age=settings.session_ttl_seconds,
    )


def clear_auth_cookies(response: Response, request: Request, settings) -> None:
    secure = cookie_secure(settings, request)
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name,
            path='/',
            httponly=httponly,
            secure=secure,
            samesite=settings.cookie_samesite,
        )


def session_from_request(request: Request) -> tuple[str | None, SessionRecord | None]:
    token = request.cookies.get(SESSION_COOKIE)
    return token, sessions.get(token)


def require_csrf(request: Request, record: SessionRecord) -> None:
    header = request.headers.get(CSRF_HEADER) or request.headers.get(CSRF_HEADER.lower()) or ''
    if not header or not hmac.compare_digest(header, record.csrf_token):
        raise HTTPException(403, 'CSRF validation failed')


def service_token_ok(settings, token: str | None) -> bool:
    expected = settings.api_token or ''
    if not expected:
        return False
    return bool(token) and hmac.compare_digest(expected, token)


def require_auth(
    request: Request,
    settings,
    *,
    x_api_token: str | None = None,
    mutating: bool = False,
) -> str:
    """Authorize an operational API call. Fail closed when unauthenticated."""
    if service_token_ok(settings, x_api_token):
        return 'service'

    token, record = session_from_request(request)
    if record is None:
        raise HTTPException(401, 'Unauthorized')

    if mutating:
        if not origin_allowed(request, settings):
            raise HTTPException(403, 'Origin not allowed')
        require_csrf(request, record)
    return record.username


def protect(settings) -> Callable:
    """FastAPI dependency factory used by route wrappers."""

    def _dep(request: Request, x_api_token: str | None = None, mutating: bool = False):
        return require_auth(request, settings, x_api_token=x_api_token, mutating=mutating)

    return _dep
