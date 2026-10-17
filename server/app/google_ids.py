"""Normalize Google Drive folder / Sheets spreadsheet links or raw IDs.

Structural URL parsing only — never fetches the pasted URL or follows redirects.
Canonical IDs are persisted; URLs are not stored.
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

# Drive/Sheets file/folder/spreadsheet IDs as returned by Google (no length cap).
_ID_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')

DRIVE_HOSTS = {'drive.google.com', 'docs.google.com'}
FOLDER_MIME = 'application/vnd.google-apps.folder'
SPREADSHEET_MIME = 'application/vnd.google-apps.spreadsheet'


class GoogleIdError(ValueError):
    """Invalid format or unsupported Google link shape."""


def _trim(value: str | None) -> str:
    return str(value or '').strip()


def looks_like_raw_id(value: str) -> bool:
    """Accept Google-style IDs without inventing a minimum length.

    Production Drive/Sheets IDs are long, but legacy fixtures and local mocks
    may use short opaque tokens. Reject only whitespace and URL punctuation.
    """
    text = _trim(value)
    if not text or any(ch.isspace() for ch in text):
        return False
    if '://' in text or '/' in text or '?' in text or '#' in text or '&' in text:
        return False
    return all(ch in _ID_CHARS for ch in text)


def _reject_resourcekey(query: dict[str, list[str]], kind: str) -> None:
    if query.get('resourcekey') or query.get('resourceKey'):
        raise GoogleIdError(
            f'This {kind} link includes a Google resourcekey. '
            'Odyssey currently requires the service account to have direct '
            'Drive/Sheets access to the resource ID; resourcekey-only links are not supported.'
        )


def _host_ok(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip('.')
    return host in DRIVE_HOSTS or host.endswith('.drive.google.com')


def parse_drive_folder_id(value: str | None) -> str:
    """Return a canonical Drive folder ID from a raw ID or trusted folder URL."""
    text = _trim(value)
    if not text:
        raise GoogleIdError('Google Drive folder link or ID is required.')
    if looks_like_raw_id(text):
        return text

    parsed = urlparse(text)
    if parsed.scheme not in ('http', 'https'):
        raise GoogleIdError('Drive folder links must use https://drive.google.com/…')
    if parsed.username or parsed.password:
        raise GoogleIdError('Drive folder links must not include user credentials.')
    if not _host_ok(parsed.hostname):
        raise GoogleIdError(
            'Drive folder links must use drive.google.com (lookalike hosts are rejected).'
        )

    query = parse_qs(parsed.query, keep_blank_values=False)
    _reject_resourcekey(query, 'Drive folder')

    path = unquote(parsed.path or '')
    segments = [part for part in path.split('/') if part]

    # /drive/folders/ID  or  /drive/u/0/folders/ID
    if 'folders' in segments:
        idx = segments.index('folders')
        if idx + 1 >= len(segments):
            raise GoogleIdError('Drive folder link is missing a folder ID.')
        folder_id = segments[idx + 1]
        if not looks_like_raw_id(folder_id):
            raise GoogleIdError('Drive folder link does not contain a valid folder ID.')
        return folder_id

    # /open?id=ID — accepted only when typed as a folder link field; type is verified later.
    if segments == ['open'] or path.rstrip('/') == '/open':
        ids = query.get('id') or []
        if len(ids) != 1 or not looks_like_raw_id(ids[0]):
            raise GoogleIdError('Drive open-link is missing a valid id= parameter.')
        return ids[0]

    raise GoogleIdError(
        'Unsupported Drive link. Paste a folder URL '
        '(drive.google.com/drive/folders/…) or the raw folder ID.'
    )


def parse_spreadsheet_id(value: str | None) -> str:
    """Return a canonical Google Sheets spreadsheet ID (never a numeric gid)."""
    text = _trim(value)
    if not text:
        raise GoogleIdError('Production Register Google Sheets link or ID is required.')
    if looks_like_raw_id(text):
        return text

    parsed = urlparse(text)
    if parsed.scheme not in ('http', 'https'):
        raise GoogleIdError('Sheets links must use https://docs.google.com/spreadsheets/…')
    if parsed.username or parsed.password:
        raise GoogleIdError('Sheets links must not include user credentials.')
    host = (parsed.hostname or '').lower().rstrip('.')
    if host != 'docs.google.com':
        raise GoogleIdError(
            'Sheets links must use docs.google.com (lookalike hosts are rejected).'
        )

    query = parse_qs(parsed.query, keep_blank_values=False)
    _reject_resourcekey(query, 'Sheets')

    path = unquote(parsed.path or '')
    segments = [part for part in path.split('/') if part]

    # /spreadsheets/d/ID/...  (ignore /d/e/ published export IDs)
    if len(segments) >= 3 and segments[0] == 'spreadsheets' and segments[1] == 'd':
        if segments[2] == 'e':
            raise GoogleIdError(
                'Published /spreadsheets/d/e/… links are not supported. '
                'Open the spreadsheet and copy the /spreadsheets/d/<ID>/ link or the raw ID.'
            )
        sheet_id = segments[2]
        if not looks_like_raw_id(sheet_id):
            raise GoogleIdError('Sheets link does not contain a valid spreadsheet ID.')
        return sheet_id

    raise GoogleIdError(
        'Unsupported Sheets link. Paste a spreadsheet URL '
        '(docs.google.com/spreadsheets/d/<ID>/…) or the raw spreadsheet ID. '
        'Do not paste a sheet tab gid.'
    )


def ensure_drive_folder(drive, folder_id: str) -> dict:
    """Confirm the ID is reachable as a Drive folder (read-only metadata)."""
    meta_fn = getattr(drive, 'file_meta', None)
    if meta_fn is None:
        # Test doubles that only implement enumerate() skip the live type check.
        return {'id': folder_id, 'mimeType': FOLDER_MIME, 'name': folder_id}
    try:
        meta = meta_fn(folder_id)
    except Exception as exc:
        status = getattr(getattr(exc, 'resp', None), 'status', None)
        if status in (403, 404):
            raise GoogleIdError(
                'Drive folder is inaccessible to the service account. '
                'Share the folder (Viewer is enough) with the service-account email.'
            ) from exc
        raise
    mime = str(meta.get('mimeType') or '')
    if mime != FOLDER_MIME:
        raise GoogleIdError(
            f'That Drive ID is not a folder (type: {mime or "unknown"}). '
            'Paste a Google Drive folder link or folder ID.'
        )
    return meta


def ensure_spreadsheet(sheets, spreadsheet_id: str) -> dict:
    """Confirm the ID is reachable as a Google spreadsheet (read-only metadata)."""
    meta_fn = getattr(sheets, 'spreadsheet_meta', None)
    if meta_fn is None:
        return {'spreadsheetId': spreadsheet_id, 'properties': {'title': spreadsheet_id}}
    try:
        meta = meta_fn(spreadsheet_id)
    except Exception as exc:
        status = getattr(getattr(exc, 'resp', None), 'status', None)
        if status in (403, 404):
            raise GoogleIdError(
                'Spreadsheet is inaccessible to the service account. '
                'Share the Production Register with the service-account email as Editor.'
            ) from exc
        raise
    return meta
