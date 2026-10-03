"""OpenAI account credit and billing failures."""
from __future__ import annotations

CREDIT_CODES = {
    'insufficient_quota',
    'billing_not_active',
    'billing_hard_limit_reached',
    'insufficient_funds',
}

_TEXT_MARKERS = (
    'insufficient_quota',
    'exceeded your current quota',
    'check your plan and billing',
    'billing hard limit',
    'insufficient credits',
    'insufficient funds',
    'out of credits',
)


def credit_alert(job_id: str) -> str:
    return (
        f'OpenAI account is out of credits. Job {job_id} has stopped and completed batches are unchanged. '
        f'Add credits to the OpenAI account, then call POST /api/jobs/{job_id}/resume. '
        'Do not start new inspection until credits are available.'
    )


def is_credit_shortage(exc: BaseException) -> bool:
    """True only for an empty balance or a billing block, not for a normal rate limit."""
    code = str(getattr(exc, 'code', '') or '').lower()
    err_type = str(getattr(exc, 'type', '') or '').lower()
    status = getattr(exc, 'status_code', None)
    body_code = ''
    body_type = ''
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        err = body.get('error') if isinstance(body.get('error'), dict) else body
        if isinstance(err, dict):
            body_code = str(err.get('code') or '').lower()
            body_type = str(err.get('type') or '').lower()
    identifiers = {code, err_type, body_code, body_type}
    if status == 402 or identifiers & CREDIT_CODES:
        return True
    text = ' '.join([str(exc), code, err_type, body_code, body_type]).lower()
    return any(marker in text for marker in _TEXT_MARKERS)


class CreditShortageError(RuntimeError):
    """Raised when OpenAI refuses the call because the account cannot be charged."""


def as_credit_error(exc: BaseException) -> BaseException:
    if isinstance(exc, CreditShortageError) or not is_credit_shortage(exc):
        return exc
    credit = CreditShortageError(credit_alert('{job_id}'))
    credit.elapsed_seconds = getattr(exc, 'elapsed_seconds', 0)
    return credit
