"""Stable governing prefix for OpenAI Responses prompt caching.

The installed SDK (openai 2.54.0) accepts prompt_cache_options.ttl only as
"30m". Official Responses docs also describe "12h", but this client cannot
send that value. No prompt_cache_key is sent from normal inspection: on
GPT-5.6 and later a key does not improve routing, and a key must not carry
filenames or client data.

A breakpoint marks where a later request may reuse a prefix. It does not
guarantee a cache hit.

Normal inspection defaults to the system-role request shape. The developer
breakpoint format is selected only when
openai_prompt_cache_optimization_enabled is explicitly true (staging tests).
"""
from __future__ import annotations

import hashlib
import json

CACHE_MODE = 'explicit'
CACHE_TTL = '30m'
CACHE_BREAKPOINT = 'stable_governing_text'
REQUEST_SHAPE_SYSTEM = 'system_role'
REQUEST_SHAPE_OPTIMIZED = 'developer_explicit_30m_breakpoint'
FLAG_NAME = 'openai_prompt_cache_optimization_enabled'

_PREAMBLE = (
    'The client runtime prompt below is the canonical inspection workflow. '
    'Return Lean Split v2 Asset Staging and Record Staging in the structured object. '
    'Do not update Google Sheets. '
    'A client-approved calibration example in the user payload is calibration for the named project and box, not a filename rule and not an answer-key extract. '
    'Inspect every authorized image. Do not copy an example when the images disagree, and do not close an item only because its folder has ended.\n\n'
)


def governing_instruction(prompt_text: str, references: list[str], output_schema: str, decisions: str) -> str:
    """The same characters as the previous system string.

    The same characters do not prove the same instruction precedence when the
    optimized path sends this text as a developer content block.
    """
    return (
        _PREAMBLE
        + prompt_text
        + '\n\n'
        + '\n\n'.join(references)
        + '\n\n'
        + output_schema
        + '\n\n'
        + decisions
    )


def prefix_fingerprint(instruction: str) -> str:
    return hashlib.sha256(instruction.encode('utf-8')).hexdigest()


def prompt_cache_optimization_enabled(settings) -> bool:
    """True only when the staging flag is explicitly enabled. Default is false."""
    return bool(getattr(settings, FLAG_NAME, False))


def request_shape_name(*, optimized: bool) -> str:
    return REQUEST_SHAPE_OPTIMIZED if optimized else REQUEST_SHAPE_SYSTEM


def system_message(instruction: str) -> dict:
    """Deployed baseline: governing text as one system-role string."""
    return {'role': 'system', 'content': instruction}


def stable_message(instruction: str) -> dict:
    return {
        'role': 'developer',
        'content': [{
            'type': 'input_text',
            'text': instruction,
            'prompt_cache_breakpoint': {'mode': 'explicit'},
        }],
    }


def dynamic_message(payload: dict, images: list) -> dict:
    return {
        'role': 'user',
        'content': [{'type': 'input_text', 'text': json.dumps(payload)}] + list(images),
    }


def cache_options() -> dict:
    return {'mode': CACHE_MODE, 'ttl': CACHE_TTL}


def build_inspection_messages(instruction: str, payload: dict, images: list, *, optimized: bool) -> list:
    """Choose the request input shape. Quality payload and schema stay elsewhere."""
    user = dynamic_message(payload, images)
    if optimized:
        return [stable_message(instruction), user]
    return [system_message(instruction), user]


def build_inspection_request(
    *,
    model: str,
    instruction: str,
    payload: dict,
    images: list,
    text_format,
    optimized: bool,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Build the Responses parse request. Model/effort/limits are unchanged by the flag."""
    request = {
        'model': model,
        'input': build_inspection_messages(instruction, payload, images, optimized=optimized),
        'text_format': text_format,
    }
    if optimized:
        request['prompt_cache_options'] = cache_options()
    if max_output_tokens:
        request['max_output_tokens'] = max_output_tokens
    if reasoning_effort:
        request['reasoning'] = {'effort': reasoning_effort}
    return request


def cache_diagnostics(instruction: str, reference_version: str, *, optimized: bool = False) -> dict:
    shape = request_shape_name(optimized=optimized)
    return {
        'prompt_fingerprint': prefix_fingerprint(instruction),
        'prompt_reference_version': reference_version or '',
        FLAG_NAME: bool(optimized),
        'request_shape': shape,
        'cache_mode': CACHE_MODE if optimized else '',
        'cache_ttl': CACHE_TTL if optimized else '',
        'cache_breakpoint': CACHE_BREAKPOINT if optimized else '',
        'prompt_cache_key_sent': False,
    }


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(block.get('text', '') for block in content if isinstance(block, dict))
    return ''
