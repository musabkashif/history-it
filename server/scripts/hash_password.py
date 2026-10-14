#!/usr/bin/env python3
"""Interactive Argon2 password hash generator for APP_LOGIN_PASSWORD_HASH."""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import hash_password  # noqa: E402


def main() -> int:
    print('HistoryIT login password hash setup')
    print('The plaintext password is not stored. Paste the hash into APP_LOGIN_PASSWORD_HASH.')
    password = getpass.getpass('Password: ')
    confirm = getpass.getpass('Confirm password: ')
    if not password:
        print('Password must not be empty.', file=sys.stderr)
        return 1
    if password != confirm:
        print('Passwords do not match.', file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
