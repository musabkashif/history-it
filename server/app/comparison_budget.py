"""Spend guard for a disposable cache comparison.

This module does not call OpenAI. Dollar figures are either absent
(provider-confirmed charges) or derived from the published Standard rate
card for gpt-5.6-sol. An unscoped cache write is never labeled as a
provider-reported 30-minute or 12-hour write.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from math import ceil

from app.inspection import CONNECTION_ATTEMPTS
from app.prompt_cache import cache_options, dynamic_message, stable_message

MODEL = 'gpt-5.6-sol'
OFFICIAL_HOSTS = {'https://api.openai.com/v1', 'https://api.openai.com/v1/'}
SHORT_CONTEXT_INPUT_LIMIT = 272_000
MODEL_MAX_INPUT = 922_000
MODEL_MAX_OUTPUT = 128_000
HIGH_DETAIL_PATCH_BUDGET = 2_500
IMAGE_MULTIPLIER = Decimal('1.2')
# The vision guide says billing can differ from the patch estimate by one token.
IMAGE_TOKEN_SLACK = 1
PLANNED_LOGICAL_REQUESTS = 6
PLANNED_USER_TEXT_BYTES = 4_000

# Standard prices per million tokens, from the API pricing page on 24 Oct 2026.
# Short context is <=272K input tokens. Long context prices the whole request.
# When the cache-comparison harness copies these figures, they remain
# application estimates — not invoice charges. An unscoped cache write never
# receives an application estimate (see classify_usage).
STANDARD_RATES = {
    'short': {
        'input': Decimal('4'),
        'cached': Decimal('0.40'),
        'output': Decimal('20'),
        'write_30m': Decimal('5'),
        'write_12h': Decimal('10'),
    },
    'long': {
        'input': Decimal('8'),
        'cached': Decimal('0.80'),
        'output': Decimal('30'),
        'write_30m': Decimal('10'),
        'write_12h': Decimal('20'),
    },
}


def high_detail_image_token_ceiling() -> int:
    """Published maximum for gpt-5.6-sol detail=high, plus one token of slack."""
    billed = ceil(HIGH_DETAIL_PATCH_BUDGET * IMAGE_MULTIPLIER)
    return int(billed) + IMAGE_TOKEN_SLACK


def _money(tokens: int, rate: Decimal) -> Decimal:
    return Decimal(tokens) * rate / Decimal(1_000_000)


class BudgetGuard:
    """Reserve a conservative ceiling before every model call.

    A hold is not released down to a point estimate. An unknown-cost
    response keeps its full hold. The guard refuses the call when the next
    hold cannot be bounded or would pass the spend or call limit.
    """

    def __init__(self, max_spend: Decimal, max_calls: int):
        self.max_spend = Decimal(max_spend)
        self.max_calls = int(max_calls)
        self.reserved = Decimal('0')
        self.calls = 0
        self.holds: list[Decimal] = []
        self.stopped = ''

    def reserve(self, ceiling: Decimal | None, reason: str = '') -> bool:
        if self.stopped:
            return False
        if ceiling is None:
            self.stopped = reason or 'A safe upper bound was not established.'
            return False
        amount = Decimal(ceiling)
        if amount < 0:
            self.stopped = 'A negative cost ceiling is not a bound.'
            return False
        if self.calls + 1 > self.max_calls:
            self.stopped = 'The call limit includes retries and split calls and has been reached.'
            return False
        if self.reserved + amount > self.max_spend:
            self.stopped = 'The next reservation would pass the maximum spend.'
            return False
        self.reserved += amount
        self.calls += 1
        self.holds.append(amount)
        return True

    def note_response(self, conservative_bound: Decimal | None) -> None:
        """Keep the larger of the pre-call hold and a post-call conservative bound.

        A missing bound leaves the pre-call hold in place and stops the run.
        """
        if not self.holds:
            self.stopped = 'A response arrived without a reservation.'
            return
        if conservative_bound is None:
            self.stopped = 'The response has no safe upper bound. Its reservation stays in the budget.'
            return
        bound = Decimal(conservative_bound)
        previous = self.holds[-1]
        if bound > previous:
            self.reserved += bound - previous
            self.holds[-1] = bound
            if self.reserved > self.max_spend:
                self.stopped = 'The response exceeded the reserved upper bound.'


def call_ceiling(*, input_ceiling: int, max_output_tokens: int, host: str, service_tier: str, detail: str, image_count: int, text_byte_ceiling_accepted: bool) -> dict:
    """Ceiling for one responses.parse call before it is sent."""
    if host not in OFFICIAL_HOSTS:
        return {'established': False, 'reason': 'The host is not the official global API, so the Standard rate card is not a bound.'}
    if service_tier != 'default':
        return {'established': False, 'reason': 'service_tier must be default. auto, fast, and flex use a rate card this ceiling does not cover.'}
    if detail != 'high':
        return {'established': False, 'reason': 'Only detail=high has the 2,500-patch ceiling used here.'}
    if image_count != 1:
        return {'established': False, 'reason': 'The comparison allows one image so a provider split cannot add calls.'}
    if not text_byte_ceiling_accepted:
        return {'established': False, 'reason': 'UTF-8 byte length is not a published tokenizer. The ceiling is not established.'}
    if max_output_tokens <= 0 or max_output_tokens > MODEL_MAX_OUTPUT:
        return {'established': False, 'reason': 'max_output_tokens is missing or above the model output limit.'}
    if input_ceiling <= 0 or input_ceiling > MODEL_MAX_INPUT:
        return {'established': False, 'reason': 'The input ceiling is missing or above the model input limit.'}
    band = 'long' if input_ceiling > SHORT_CONTEXT_INPUT_LIMIT else 'short'
    rates = STANDARD_RATES[band]
    # Any input token may be a cache write, and the usage object may not name
    # the lifetime. The 12-hour write rate is the highest Standard input rate
    # on this card. It is a bound, not a reported retention.
    amount = _money(input_ceiling, rates['write_12h']) + _money(max_output_tokens, rates['output'])
    return {
        'established': True,
        'reason': '',
        'band': band,
        'input_ceiling': input_ceiling,
        'output_ceiling': max_output_tokens,
        'usd': amount,
        'rate_basis': 'standard_12h_write_on_all_input_tokens',
    }


def classify_usage(record: dict) -> dict:
    """Separate provider facts, an application estimate, and a conservative bound."""
    provider_retention = str(record.get('cache_write_retention') or 'unknown')
    result = {
        'provider_confirmed_usd': None,
        'provider_reported_retention': provider_retention,
        'application_estimate_usd': None,
        'conservative_bound_usd': None,
        'bound_rate_used_for_unscoped_write': None,
        'established': False,
    }
    if record.get('usage_identity_gap') or record.get('uncached_input_tokens') == '':
        return result
    if provider_retention in {'24h', 'mixed'}:
        return result
    try:
        input_tokens = int(record.get('input_tokens'))
        output_tokens = int(record.get('output_tokens'))
    except (TypeError, ValueError):
        return result
    band = 'long' if input_tokens > SHORT_CONTEXT_INPUT_LIMIT else 'short'
    rates = STANDARD_RATES[band]
    uncached = int(record.get('uncached_input_tokens') or 0)
    cached = int(record.get('cached_input_tokens') or 0)
    write_30 = record.get('cache_write_30m_tokens')
    write_12 = record.get('cache_write_12h_tokens')
    unscoped = record.get('cache_write_tokens')
    amount = _money(uncached, rates['input']) + _money(cached, rates['cached']) + _money(output_tokens, rates['output'])
    estimate = amount
    if provider_retention == '30m':
        amount += _money(int(write_30 or 0), rates['write_30m'])
        estimate = amount
    elif provider_retention == '12h':
        amount += _money(int(write_12 or 0), rates['write_12h'])
        estimate = amount
    elif provider_retention == 'unknown' and unscoped not in (None, '', 0):
        amount += _money(int(unscoped), rates['write_12h'])
        estimate = None
        result['bound_rate_used_for_unscoped_write'] = '12h_standard_rate_not_provider_reported'
    elif provider_retention == 'unknown':
        estimate = amount
    else:
        return result
    result['conservative_bound_usd'] = amount
    result['application_estimate_usd'] = estimate
    result['established'] = True
    result['band'] = band
    return result


def comparison_requests(instruction: str, payload: dict, images: list) -> dict:
    """Build the two arms. The baseline keeps the old system string."""
    return {
        'baseline': {
            'model': MODEL,
            'service_tier': 'default',
            'input': [{'role': 'system', 'content': instruction}, dynamic_message(payload, images)],
            'prompt_cache_key': 'compare-baseline',
        },
        'explicit': {
            'model': MODEL,
            'service_tier': 'default',
            'input': [stable_message(instruction), dynamic_message(payload, images)],
            'prompt_cache_options': cache_options(),
            'prompt_cache_key': 'compare-explicit',
        },
    }


def proposed_comparison(*, instruction_bytes: int, max_output_tokens: int) -> dict:
    """Smallest comparison: one disposable image, warm-up plus two follow-ups, two arms."""
    if instruction_bytes < 0:
        raise ValueError('instruction_bytes')
    input_ceiling = instruction_bytes + PLANNED_USER_TEXT_BYTES + high_detail_image_token_ceiling()
    one = call_ceiling(
        input_ceiling=input_ceiling,
        max_output_tokens=max_output_tokens,
        host='https://api.openai.com/v1',
        service_tier='default',
        detail='high',
        image_count=1,
        text_byte_ceiling_accepted=True,
    )
    max_calls = PLANNED_LOGICAL_REQUESTS * CONNECTION_ATTEMPTS
    if not one['established']:
        return {'established': False, 'reason': one['reason'], 'max_calls': max_calls}
    per_call = one['usd']
    max_spend = (per_call * max_calls).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    return {
        'established': True,
        'input_ceiling': input_ceiling,
        'per_call_usd': per_call,
        'logical_requests': PLANNED_LOGICAL_REQUESTS,
        'connection_attempts': CONNECTION_ATTEMPTS,
        'max_calls': max_calls,
        'max_spend_usd': max_spend,
        'band': one['band'],
        'order': ('explicit warm-up', 'explicit follow-up 1', 'explicit follow-up 2', 'baseline warm-up', 'baseline follow-up 1', 'baseline follow-up 2'),
    }
