"""Standalone cache-comparison harness.

Normal inspection does not import this module. Calling prepare() does not
contact OpenAI. execute() refuses unless a test or an operator explicitly
enables a paid run, and the real fixture is blocked until a defensible
ceiling fits in $20.

A clean comparison isolates each arm with a unique prompt_cache_key under a
fresh run id. Production inspection still sends no prompt_cache_key.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.comparison_budget import STANDARD_RATES, classify_usage
from app.models import InspectionOutput
from app.prompt_cache import cache_options, dynamic_message, governing_instruction, prefix_fingerprint, stable_message
from app.usage_accounting import parse_provider_usage

# System-role request shape source. Staging remains at STAGING_COMMIT without
# the undeployed developer-breakpoint format.
DEPLOYED_COMMIT = 'ee1f7d44436e824eef9290a08db9269e093e716e'
STAGING_COMMIT = '0f5fe59e8b359809b75c31663243c0a4a54dc641'
MAX_CALLS = 6
MAX_SPEND = Decimal('20')
CALLS_PER_ARM = 3
QUALITY = {
    'model': 'gpt-5.6-sol',
    'reasoning_effort': 'high',
    'image_detail': 'high',
    'max_output_tokens': 60000,
}
ROLE_DIFFERENCE = (
    'Baseline, matching staging 0f5fe59 / ee1f7d4 system-role shape, sends the '
    'governing text as a system role whose content is one string. The optimized '
    'arm sends the same characters as a developer role content block with an '
    'explicit cache breakpoint. The Responses API does not say those roles are '
    'equivalent.'
)
# Deployed requests omit service_tier, which means auto. The project API does
# not return the project tier. The comparison pins the same Standard tier on
# both arms and does not change application settings.
COMPARISON_SERVICE_TIER = 'default'
# Comparison-only rates. These are application estimates for the harness, not
# invoice charges and not staging Settings values (which may be zero).
COMPARISON_APPLICATION_ESTIMATE_RATES = {
    'label': 'application_estimate_not_invoice_charge',
    'basis': 'OpenAI Standard rate card for gpt-5.6-sol short context (<=272k input), copied for this comparison only',
    'currency': 'USD',
    'per_million_tokens': {
        'uncached_input': STANDARD_RATES['short']['input'],
        'cached_input': STANDARD_RATES['short']['cached'],
        'output': STANDARD_RATES['short']['output'],
        'cache_write_30m': STANDARD_RATES['short']['write_30m'],
        'cache_write_12h': STANDARD_RATES['short']['write_12h'],
    },
    'unscoped_cache_write': 'never_priced_as_application_estimate',
}
LOCAL_VIEWS = Path(r'C:\Users\asa\AppData\Local\Temp\historyit_model_views')
FIXTURE_CASES = (
    {
        'local_file': '004-model-input.jpg',
        'source_filename': 'TriDelta_Year2_Chapters_Box31_F04_004.jpg',
        'pixels': '2048x1536',
        'lookup': 'example',
    },
    {
        'local_file': '002-model-input.jpg',
        'source_filename': 'TriDelta_Year2_Chapters_Box31_F04_002.jpg',
        'pixels': '2048x1536',
        'lookup': 'F04_002.jpg',
    },
    {
        'local_file': '016b-model-input.jpg',
        'source_filename': 'TriDelta_Year2_Chapters_Box31_F04_Photograph_016b.jpg',
        'pixels': '2048x1365',
        'lookup': '_Photograph_016b',
    },
)
# Tasks.md records this model-input size for Photograph_016b. 004 and 002 are
# the stored model inputs from the same directory; that table does not list them.
PUBLISHED_MODEL_INPUT = {'016b-model-input.jpg': '2048x1365'}
ARM_ORDER = ('optimized', 'baseline')
PROMPT_CACHE_KEY_MAX_LEN = 64
PROMPT_CACHE_KEY_PREFIX = 'hitcc'
FAILED_26_OCT_RUN = {
    'date': '2026-10-26',
    'status': 'unknown_evidence',
    'reuse': False,
    'note': (
        'Failed while saving the summary; no events jsonl and no response IDs. '
        'Spend and generation-call count remain unknown. Do not reuse as comparison evidence.'
    ),
}
PRIOR_CONTAMINATED_RUN = {
    'id': 'compare-20261029T072738Z-0453f327',
    'reuse': False,
    'note': 'Baseline warm-up was contaminated. Keep as historical evidence only.',
}
INCOMPLETE_CLEAN_RUN = {
    'id': 'clean-20261029T174759Z-d99cf263',
    'status': 'incomplete_unknown_outcome',
    'reuse': False,
    'note': (
        'Stopped on the first generation call: prompt_cache_key exceeded the API '
        '64-character limit (71/70). No usage returned; quality and savings unknown. '
        'Do not reuse its records.'
    ),
}
EXECUTION_COMMAND = (
    r'D:\Integer\HistoryIT\history-it\server\.venv\Scripts\python.exe -m harness.cache_comparison --allow-paid-calls'
)
ACCEPTANCE_RULE = (
    'Pass only when all three quality outcomes pass in both arms; baseline and '
    'optimized warm-ups are uncontaminated; optimized follow-ups show cache '
    'reads; optimized total application-estimate cost is lower than baseline; '
    'and that difference is larger than documented uncertainty (overlapping '
    'estimate/bound ranges or any unknown estimate). Otherwise the result is inconclusive.'
)


def _leading_text(node):
    """Return the opening string constants, stopping at the first non-constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _leading_text(node.left)
        if left is None:
            return None
        text, pure = left
        if pure and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return text + node.right.value, True
        return text, False
    return None


def _dict_keys(node):
    if not isinstance(node, ast.Dict):
        return None
    keys = []
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        keys.append(key.value)
    return keys


def deployed_contract_from_source(source: str) -> dict:
    """Read the request shape from the deployed inspection.py source."""
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == '_inspect_once'
    )
    instruction = None
    payload_keys = None
    request_keys = None
    system_role = None
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'instruction' for target in node.targets):
            leading = _leading_text(node.value)
            instruction = None if leading is None else leading[0]
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'payload' for target in node.targets):
            payload_keys = _dict_keys(node.value)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'request' for target in node.targets):
            request_keys = _dict_keys(node.value)
            roles = [
                item.value
                for item in ast.walk(node.value)
                if isinstance(item, ast.Constant) and item.value in {'system', 'user', 'developer'}
            ]
            system_role = roles[0] if roles else None
    if not instruction or not payload_keys or request_keys != ['model', 'input', 'text_format'] or system_role != 'system':
        raise ValueError('Deployed source does not match the expected request assignment.')
    function_source = ast.get_source_segment(source, function) or ''
    if 'prompt_cache_options' in function_source or 'prompt_cache_breakpoint' in function_source:
        raise ValueError('Deployed _inspect_once contains prompt_cache_options.')
    return {
        'commit': DEPLOYED_COMMIT,
        'preamble': instruction,
        'payload_keys': payload_keys,
        'request_keys': request_keys,
        'system_role': system_role,
        'image_detail': 'high',
    }


def baseline_instruction(contract: dict, prompt_text: str, references: list[str], output_schema: str, decisions: str) -> str:
    return (
        contract['preamble']
        + prompt_text
        + '\n\n'
        + '\n\n'.join(references)
        + '\n\n'
        + output_schema
        + '\n\n'
        + decisions
    )


def comparison_payload(job: str, batch: str, context: dict, rules, sources, approved) -> dict:
    prior = context.get('prior_open_item') if isinstance(context, dict) else None
    prior_text = 'None' if prior in (None, '', 'None') else str(prior)
    existing = []
    if isinstance(context, dict) and prior_text != 'None':
        existing = context.get('existing_open_record') or context.get('prior_open_item_context') or []
    return {
        'job_id': job,
        'batch_id': batch,
        'project': context.get('project') if isinstance(context, dict) else '',
        'run_context': context.get('run_context') if isinstance(context, dict) else [],
        'batch_queue_row': context.get('batch_queue_row') if isinstance(context, dict) else {},
        'manifest': context.get('manifest') if isinstance(context, dict) else [],
        'prior_open_item': prior_text,
        'prior_open_item_source': (context.get('prior_open_item_source') if isinstance(context, dict) else '') or 'Batch Queue Prior Open Item',
        'existing_open_record': existing,
        'prior_open_item_context': existing,
        'project_classification_rules': rules,
        'authorized_project_state': context.get('authorized_project_state') if isinstance(context, dict) else {},
        'approved_project_reference': approved,
        'folder_code': context.get('folder_code') if isinstance(context, dict) else '',
        'authorized_filenames': [source.name for source in sources],
    }


def inline_image_parts(sources, blobs) -> list:
    """Deployed inline branch, used when the client has no files.create."""
    import base64
    parts = []
    for source, data, mime in blobs:
        parts.append({'type': 'input_text', 'text': f'Source filename: {source.name}; Drive ID: {source.id}'})
        parts.append({
            'type': 'input_image',
            'image_url': f'data:{mime};base64,{base64.b64encode(data).decode()}',
            'detail': QUALITY['image_detail'],
        })
    return parts


def _common_request(model: str, messages: list, max_output_tokens: int, effort: str) -> dict:
    if model != QUALITY['model'] or effort != QUALITY['reasoning_effort'] or max_output_tokens != QUALITY['max_output_tokens']:
        raise ValueError('The comparison cannot change model, reasoning effort, or the output limit.')
    return {
        'model': model,
        'input': messages,
        'text_format': InspectionOutput,
        'max_output_tokens': max_output_tokens,
        'reasoning': {'effort': effort},
    }


def new_comparison_run_id() -> str:
    """Fresh id for one clean comparison. Never reuse a prior run folder or key."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'clean-{stamp}-{secrets.token_hex(4)}'


def arm_prompt_cache_key(run_id: str, arm: str) -> str:
    """Isolate OpenAI prompt-cache accounting for one arm of one run.

    Format: hitcc-{arm_tag}-{sha256(run_id|arm)[:40]}. Always <=64 characters.
    Keys never include filenames or client data. Distinct digests keep baseline
    and optimized namespaces separate and prevent reuse across run ids.
    """
    if arm not in ARM_ORDER:
        raise ValueError(f'Unsupported comparison arm: {arm}')
    text = str(run_id or '').strip()
    if not text or '/' in text or '\\' in text or '..' in text:
        raise ValueError('comparison run id is missing or unsafe')
    arm_tag = 'o' if arm == 'optimized' else 'b'
    digest = hashlib.sha256(f'{text}|{arm}'.encode('utf-8')).hexdigest()[:40]
    key = f'{PROMPT_CACHE_KEY_PREFIX}-{arm_tag}-{digest}'
    if len(key) > PROMPT_CACHE_KEY_MAX_LEN:
        raise RuntimeError(f'prompt_cache_key length {len(key)} exceeds {PROMPT_CACHE_KEY_MAX_LEN}')
    return key


def assert_prompt_cache_key_allowed(request: dict) -> None:
    """Refuse a provider call when the comparison cache key is missing or too long."""
    key = request.get('prompt_cache_key')
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError('Comparison request is missing a prompt_cache_key. No provider call was made.')
    if len(key) > PROMPT_CACHE_KEY_MAX_LEN:
        raise RuntimeError(
            f'prompt_cache_key length {len(key)} exceeds {PROMPT_CACHE_KEY_MAX_LEN}. '
            'No provider call was made.'
        )


def evidence_folder_for_run(run_id: str) -> Path:
    return Path(__file__).resolve().parent / 'reports' / 'clean-runs' / run_id


def baseline_request(instruction: str, payload: dict, images: list, model: str, max_output_tokens: int, effort: str, *, prompt_cache_key: str | None = None) -> dict:
    request = _common_request(model, [
        {'role': 'system', 'content': instruction},
        {'role': 'user', 'content': [{'type': 'input_text', 'text': json.dumps(payload)}] + list(images)},
    ], max_output_tokens, effort)
    if prompt_cache_key:
        request['prompt_cache_key'] = prompt_cache_key
    return request


def optimized_request(instruction: str, payload: dict, images: list, model: str, max_output_tokens: int, effort: str, *, prompt_cache_key: str | None = None) -> dict:
    request = _common_request(model, [
        stable_message(instruction),
        dynamic_message(payload, images),
    ], max_output_tokens, effort)
    request['prompt_cache_options'] = cache_options()
    if prompt_cache_key:
        request['prompt_cache_key'] = prompt_cache_key
    return request


def with_comparison_tier(request: dict) -> dict:
    """Pin Standard on a comparison copy. The application request is unchanged."""
    pinned = dict(request)
    pinned['service_tier'] = COMPARISON_SERVICE_TIER
    return pinned


def load_fixture_expectations(calibration: dict) -> dict:
    """Expected fields for the local F04 frames those rules already name."""
    if calibration.get('reference_id') != 'tri-delta-chapters-box31-f04-calibration':
        raise ValueError('The comparison must keep the reviewed Tri Delta Chapters Box 31 F04 calibration scope.')
    if calibration.get('version') != '2026-10-24.1':
        raise ValueError('The comparison must keep calibration version 2026-10-24.1.')
    example = calibration['examples'][0]
    rules = {rule['contains']: rule for rule in calibration['filename_rules']}
    images = []
    for case in FIXTURE_CASES:
        path = LOCAL_VIEWS / case['local_file']
        if case['lookup'] == 'example':
            if case['source_filename'] not in example['filenames']:
                raise ValueError('F04_004 is not the reviewed descriptive-reference example.')
            expected = {
                'record_kind': example['outcome']['record_kind'],
                'template': example['outcome']['template'],
                'production_tasks': example['outcome']['production_tasks'],
            }
        else:
            rule = rules[case['lookup']]
            expected = {'record_kind': rule['record_kind'], 'action': rule['action']}
            if rule.get('note_must_include'):
                expected['note_must_include'] = list(rule['note_must_include'])
        images.append({
            'path': str(path),
            'source_filename': case['source_filename'],
            'pixels': case['pixels'],
            'expected': expected,
        })
    return {
        'images': images,
        'writes_client_registers': False,
        'calibration_scope': {
            'reference_id': calibration['reference_id'],
            'version': calibration['version'],
            'project': calibration['project'],
            'box': calibration['box'],
            'folder': 'F04',
            'not_an_executable_rule': calibration.get('not_an_executable_rule') is True,
        },
        'can_establish': [
            'Schema-valid output for these three frames.',
            'Each source filename is echoed once.',
            'F04_004 is Metadata Only with no production task.',
            'F04_002 requests an Archival Border Crop.',
            'Photograph_016b requests Rotate and an Archival Border Crop, and the note includes 180.',
        ],
        'requires_box_acceptance': [
            'F04_004 and F04_005 stay one descriptive reference and its blank verso. F04_005 is not in the local views.',
            'F04_003, F04_006, and F04_007, and Photograph_008 through Photograph_021 except the one 016b frame.',
            'Attached-note, white physical border, and dark-edge notes.',
            'A calibration mismatch stops the batch before commit.',
            'The system-to-developer role change is safe for a whole box.',
            'The cache change reduces cost.',
        ],
    }


class _Named:
    def __init__(self, name: str):
        self.name = name
        self.id = 'local-model-input'


def verify_prepared_images() -> list[dict]:
    """Confirm the local files are the stored model inputs, unchanged by preparation."""
    from io import BytesIO

    from PIL import Image

    from app.images import prepare_model_image

    verified = []
    for case in FIXTURE_CASES:
        path = LOCAL_VIEWS / case['local_file']
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            pixels = f'{image.size[0]}x{image.size[1]}'
        if pixels != case['pixels']:
            raise ValueError(f'{case["local_file"]} is {pixels}, not the recorded {case["pixels"]}.')
        published = PUBLISHED_MODEL_INPUT.get(case['local_file'])
        if published and pixels != published:
            raise ValueError(f'{case["local_file"]} does not match the published model input {published}.')
        prepared, _mime, mode = prepare_model_image(data)
        if mode != 'original' or prepared != data:
            raise ValueError(f'{case["local_file"]} is not the unchanged model-input file.')
        verified.append({
            'path': str(path),
            'source_filename': case['source_filename'],
            'pixels': pixels,
            'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'mode': mode,
        })
    return verified


def _shared_context() -> dict:
    return {
        'project': 'Tri Delta Chapters',
        'folder_code': 'F04',
        'prior_open_item': 'None',
        'box': 'Chapters Box 31',
    }


def comparison_sequence(instruction: str, calibration: dict, image_parts: dict, *, run_id: str | None = None) -> list[dict]:
    """Three optimized calls, then three baseline calls. The warm-up stays in each arm total.

    Each arm receives its own prompt_cache_key under run_id so the arms cannot
    share a prior cache entry with each other or with an earlier comparison.
    """
    fingerprint = prefix_fingerprint(instruction)
    run = run_id or new_comparison_run_id()
    namespaces = {arm: arm_prompt_cache_key(run, arm) for arm in ARM_ORDER}
    if namespaces['optimized'] == namespaces['baseline']:
        raise RuntimeError('Comparison arms must not share a prompt_cache_key.')
    steps = []
    order = 0
    for arm in ARM_ORDER:
        for index, case in enumerate(FIXTURE_CASES):
            order += 1
            payload = comparison_payload(
                'compare-f04',
                f'compare-{index + 1}',
                _shared_context(),
                [],
                [_Named(case['source_filename'])],
                calibration,
            )
            images = list(image_parts[case['source_filename']])
            builder = optimized_request if arm == 'optimized' else baseline_request
            request = with_comparison_tier(builder(
                instruction,
                payload,
                images,
                QUALITY['model'],
                QUALITY['max_output_tokens'],
                QUALITY['reasoning_effort'],
                prompt_cache_key=namespaces[arm],
            ))
            steps.append({
                'order': order,
                'arm': arm,
                'position_in_arm': index + 1,
                'warm_up': index == 0,
                'included_in_arm_total': True,
                'source_filename': case['source_filename'],
                'governing_fingerprint': fingerprint,
                'run_id': run,
                'prompt_cache_key': namespaces[arm],
                'request': request,
                'observation': {
                    'started_at': None,
                    'completed_at': None,
                    'response_id': None,
                    'service_tier': None,
                    'usage': None,
                    'parsed_usage': None,
                    'contamination': None,
                    'cache_state': None,
                    'application_estimate_usd': None,
                    'conservative_bound_usd': None,
                    'cost_known': None,
                    'cost_basis': None,
                    'attempt': None,
                    'status': None,
                    'failure_reason': None,
                    'billed': None,
                },
            })
    return steps


def record_observation(step: dict, response, started_at: str, completed_at: str, *, attempt: int = 1) -> dict:
    """Store parsed usage facts. An unscoped write stays an unknown estimate."""
    usage = getattr(response, 'usage', None)
    usage_dump = usage.model_dump() if hasattr(usage, 'model_dump') else dict(usage or {})
    response_id = getattr(response, 'id', None) or ''
    parsed = parse_provider_usage(
        usage_dump,
        response_id=response_id,
        attempt=attempt,
        part=1,
        status='Received',
    )
    cached = parsed.get('cached_input_tokens')
    observation = step['observation']
    contamination = None
    if step['warm_up'] and cached not in (None, 0, ''):
        contamination = 'warm-up returned cache-read tokens'
    cache_state = 'contaminated_warm_up' if contamination else (
        'cache_read' if cached not in (None, 0, '') else (
            'cache_write_or_miss' if parsed.get('cache_write_tokens') not in (None, '', 0)
            or parsed.get('cache_write_30m_tokens') not in (None, '', 0)
            or parsed.get('cache_write_12h_tokens') not in (None, '', 0)
            else 'no_cache_activity'
        )
    )
    classified = classify_usage(parsed)
    observation.update({
        'started_at': started_at,
        'completed_at': completed_at,
        'response_id': parsed.get('response_id') or response_id or None,
        'service_tier': getattr(response, 'service_tier', None),
        'usage': usage_dump,
        'parsed_usage': {
            'uncached_input_tokens': parsed.get('uncached_input_tokens'),
            'cached_input_tokens': parsed.get('cached_input_tokens'),
            'cache_write_30m_tokens': parsed.get('cache_write_30m_tokens'),
            'cache_write_12h_tokens': parsed.get('cache_write_12h_tokens'),
            'cache_write_tokens': parsed.get('cache_write_tokens'),
            'cache_write_retention': parsed.get('cache_write_retention'),
            'output_tokens': parsed.get('output_tokens'),
            'input_tokens': parsed.get('input_tokens'),
        },
        'contamination': contamination,
        'cache_state': cache_state,
        'attempt': parsed.get('attempt'),
        'status': parsed.get('status'),
        'failure_reason': parsed.get('failure_reason') or None,
        'billed': parsed.get('billed'),
        'application_estimate_usd': classified['application_estimate_usd'],
        'conservative_bound_usd': classified['conservative_bound_usd'],
        'cost_known': classified['application_estimate_usd'] is not None,
        'cost_basis': COMPARISON_APPLICATION_ESTIMATE_RATES['label'] if classified['application_estimate_usd'] is not None else (
            'unknown_unscoped_or_incomplete' if classified.get('bound_rate_used_for_unscoped_write') else 'unknown'
        ),
        'pricing_inputs': COMPARISON_APPLICATION_ESTIMATE_RATES,
    })
    return observation


def savings_are_inconclusive(left: dict, right: dict) -> bool:
    """Unknown costs, or overlapping ranges, do not support a savings claim."""
    needed = ('application_estimate_usd', 'conservative_bound_usd')
    if any(left.get(key) is None or right.get(key) is None for key in needed):
        return True
    left_low = min(left['application_estimate_usd'], left['conservative_bound_usd'])
    left_high = max(left['application_estimate_usd'], left['conservative_bound_usd'])
    right_low = min(right['application_estimate_usd'], right['conservative_bound_usd'])
    right_high = max(right['application_estimate_usd'], right['conservative_bound_usd'])
    return left_low <= right_high and right_low <= left_high


def evaluate_clean_acceptance(report: dict) -> dict:
    """Apply the clean-comparison acceptance rule. Failure means inconclusive."""
    steps = list(report.get('steps') or [])
    reasons = []
    if len(steps) != MAX_CALLS:
        reasons.append(f'expected {MAX_CALLS} steps, found {len(steps)}')
    quality_ok = True
    for arm in ARM_ORDER:
        arm_steps = [step for step in steps if step.get('arm') == arm]
        if len(arm_steps) != CALLS_PER_ARM:
            quality_ok = False
            reasons.append(f'{arm} missing calls')
            continue
        for step in arm_steps:
            quality = step.get('quality') or {}
            if not quality.get('pass'):
                quality_ok = False
                reasons.append(f"{arm}:{step.get('source_filename')} quality failed")
    if not quality_ok and 'quality failed' not in ' '.join(reasons):
        reasons.append('quality outcomes incomplete')

    warm_ups = [step for step in steps if step.get('warm_up')]
    warm_ok = True
    for step in warm_ups:
        contamination = (step.get('observation') or {}).get('contamination')
        if contamination:
            warm_ok = False
            reasons.append(f"{step.get('arm')} warm-up contaminated: {contamination}")
        cached = ((step.get('observation') or {}).get('parsed_usage') or {}).get('cached_input_tokens')
        if cached not in (None, 0, ''):
            warm_ok = False
            if f"{step.get('arm')} warm-up contaminated" not in ' '.join(reasons):
                reasons.append(f"{step.get('arm')} warm-up had cache reads")

    optimized_follow = [
        step for step in steps
        if step.get('arm') == 'optimized' and not step.get('warm_up')
    ]
    follow_reads_ok = True
    if len(optimized_follow) != 2:
        follow_reads_ok = False
        reasons.append('optimized follow-ups missing')
    else:
        for step in optimized_follow:
            cached = ((step.get('observation') or {}).get('parsed_usage') or {}).get('cached_input_tokens')
            if cached in (None, 0, ''):
                follow_reads_ok = False
                reasons.append(f"optimized follow-up {step.get('source_filename')} had no cache read")

    optimized = report.get('optimized') or {}
    baseline = report.get('baseline') or {}
    if savings_are_inconclusive(optimized, baseline):
        cost_ok = False
        reasons.append('cost difference is unknown or within documented uncertainty')
    else:
        cost_ok = optimized['conservative_bound_usd'] < baseline['application_estimate_usd']
        if not cost_ok:
            reasons.append('optimized estimated cost is not lower than baseline beyond uncertainty')

    passed = bool(quality_ok and warm_ok and follow_reads_ok and cost_ok)
    return {
        'result': 'pass' if passed else 'inconclusive',
        'acceptance_rule': ACCEPTANCE_RULE,
        'quality_pass_both_arms': quality_ok,
        'warm_ups_uncontaminated': warm_ok,
        'optimized_follow_ups_show_cache_reads': follow_reads_ok,
        'optimized_estimate_lower_beyond_uncertainty': cost_ok,
        'reasons': [] if passed else reasons,
        'failed_26_oct_reused': False,
        'prior_contaminated_run_reused': False,
    }


def reservations_allow_generation(ceilings: list) -> bool:
    """True only when every generation reservation is present and their sum is within $20."""
    if not ceilings or len(ceilings) > MAX_CALLS:
        return False
    total = Decimal(0)
    for ceiling in ceilings:
        if ceiling is None:
            return False
        total += Decimal(ceiling)
    return total <= MAX_SPEND


class ComparisonHarness:
    """Six-call budget. A timeout or any exception stops the run."""

    def __init__(self):
        self.calls = 0
        self.reserved = Decimal('0')
        self.stopped = ''
        self.holds: list[Decimal] = []

    def reserve(self, ceiling: Decimal | None) -> bool:
        if self.stopped:
            return False
        if ceiling is None:
            self.stopped = 'A defensible upper bound was not established.'
            return False
        amount = Decimal(ceiling)
        if self.calls + 1 > MAX_CALLS:
            self.stopped = 'The six-call limit has been reached.'
            return False
        if self.reserved + amount > MAX_SPEND:
            self.stopped = 'The next reservation would pass $20.'
            return False
        self.reserved += amount
        self.calls += 1
        self.holds.append(amount)
        return True

    def note_unknown(self) -> None:
        """An unknown outcome keeps its reservation and stops the run."""
        self.stopped = 'An unknown or failed outcome stops the run. Its reservation stays in the budget.'

    def execute(self, client, requests: list[dict], ceilings: list[Decimal | None], *, allow_paid_calls: bool = False):
        if not allow_paid_calls:
            raise RuntimeError('Paid calls are disabled. The harness was not run.')
        if not reservations_allow_generation(ceilings):
            self.stopped = 'Counted-request reservations exceed $20 or are missing. No generation call was made.'
            return {'calls': 0, 'reserved': self.reserved, 'stopped': self.stopped, 'generation_started': False}
        retries = getattr(client, 'max_retries', 0)
        if retries not in (0, None):
            raise RuntimeError('SDK retries must be disabled.')
        if len(requests) > MAX_CALLS:
            raise RuntimeError('The plan has more than six provider calls.')
        for request, ceiling in zip(requests, ceilings):
            try:
                assert_prompt_cache_key_allowed(request)
            except RuntimeError as exc:
                self.stopped = str(exc)
                break
            if not self.reserve(ceiling):
                break
            try:
                client.responses.parse(**request)
            except Exception:
                self.note_unknown()
                break
        return {'calls': self.calls, 'reserved': self.reserved, 'stopped': self.stopped}


def prepare(contract_source: str, calibration: dict) -> dict:
    """Describe the blocked clean comparison. This function makes no provider call."""
    contract = deployed_contract_from_source(contract_source)
    expectations = load_fixture_expectations(calibration)
    example_run_id = 'clean-PREVIEW-not-for-execution'
    return {
        'ready': False,
        'paid_calls_made': 0,
        'executed': False,
        'count_endpoint_called': False,
        'staging_commit_unchanged': STAGING_COMMIT,
        'baseline_request_format': 'system_role_string',
        'optimized_request_format': 'developer_explicit_30m_breakpoint',
        'max_calls': MAX_CALLS,
        'max_spend_usd': MAX_SPEND,
        'input_tokens': None,
        'maximum_reserved_spend_usd': MAX_SPEND,
        'comparison_service_tier': COMPARISON_SERVICE_TIER,
        'quality': dict(QUALITY),
        'role_difference': ROLE_DIFFERENCE,
        'system_role': contract['system_role'],
        'expectations': expectations,
        'isolation_method': {
            'mechanism': 'prompt_cache_key per arm under a fresh run id',
            'key_format': f'{PROMPT_CACHE_KEY_PREFIX}-{{o|b}}-<sha256(run_id|arm)[:40]>',
            'key_max_len': PROMPT_CACHE_KEY_MAX_LEN,
            'evidence_folder': 'server/harness/reports/clean-runs/<run_id>/',
            'baseline_key_example': arm_prompt_cache_key(example_run_id, 'baseline'),
            'optimized_key_example': arm_prompt_cache_key(example_run_id, 'optimized'),
            'warm_up_proof': 'each arm warm-up must report cached_input_tokens=0 or the arm is contaminated',
            'does_not_reuse': [
                FAILED_26_OCT_RUN['date'],
                PRIOR_CONTAMINATED_RUN['id'],
                INCOMPLETE_CLEAN_RUN['id'],
            ],
        },
        'pricing_inputs': COMPARISON_APPLICATION_ESTIMATE_RATES,
        'acceptance_rule': ACCEPTANCE_RULE,
        'sequence_per_arm': ['warm-up', 'follow-up-1', 'follow-up-2'],
        'arm_order': list(ARM_ORDER),
        'stop_before_generation_if_evidence_unsaved': True,
        'failed_26_oct_run': FAILED_26_OCT_RUN,
        'prior_contaminated_run': PRIOR_CONTAMINATED_RUN,
        'incomplete_clean_run': INCOMPLETE_CLEAN_RUN,
        'input_count': {
            'endpoint': 'POST /v1/responses/input_tokens',
            'called': False,
            'billing': 'not documented',
            'known_charge': False,
        },
        'authorization_needed': (
            'Written permission to count and then generate these six requests for the three local '
            'Box 31/F04 frames, with no client register write, no retry of a frozen job, no staging '
            'deploy of the optimized request format, and acceptance that dollar figures are '
            'application estimates from COMPARISON_APPLICATION_ESTIMATE_RATES — not invoice charges.'
        ),
        'execution_command': EXECUTION_COMMAND,
        'warm_up_included_in_arm_total': True,
        'savings_claim': False,
        'count_charges_undocumented': True,
        'generation_cap_covers_count_calls': False,
        'writes_registers': False,
        'frozen_jobs_resumed': False,
    }


def _scrub(text: str) -> str:
    redacted = text[:800]
    for name in ('OPENAI_API_KEY',):
        secret = __import__('os').environ.get(name) or ''
        if secret:
            redacted = redacted.replace(secret, '[REDACTED]')
    return redacted


def _working_tree_fingerprint() -> dict:
    import subprocess
    root = Path(__file__).resolve().parents[2]
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    diff = subprocess.check_output(['git', 'diff', 'HEAD'], cwd=root)
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=root, text=True).splitlines()
    digest = hashlib.sha256()
    digest.update(diff)
    for relative in sorted(untracked):
        path = root / relative
        if path.is_file() and 'harness' in relative and relative.endswith(('.json', '.py')) and 'reports' not in relative:
            digest.update(relative.encode())
            digest.update(path.read_bytes())
    return {'head': head, 'working_tree_fingerprint': digest.hexdigest()}


def _governing_instruction():
    from app.config import Settings
    from app.decisions import decision_instructions
    from app.inspection import OUTPUT_SCHEMA, _docx_text, _runtime_prompt

    settings = Settings()
    prompt_text, _name = _runtime_prompt(settings)
    docs = Path(settings.governing_docs_dir)
    references = []
    for path in sorted(docs.iterdir()):
        if path.suffix.lower() in {'.md', '.txt'}:
            references.append(path.name + '\n' + path.read_text(encoding='utf-8'))
        elif path.suffix.lower() == '.docx':
            references.append(path.name + '\n' + _docx_text(path))
    if not references:
        raise ValueError('No readable governing documents')
    return governing_instruction(prompt_text, references, OUTPUT_SCHEMA, decision_instructions()), settings


def _count_input_tokens(client, request: dict) -> int:
    from openai.lib._parsing._responses import type_to_text_format_param

    assert_prompt_cache_key_allowed(request)
    counted = client.responses.input_tokens.count(
        model=request['model'],
        input=request['input'],
        reasoning=request['reasoning'],
        text={'format': type_to_text_format_param(request['text_format'])},
    )
    return int(counted.input_tokens)


def _generation_ceiling(input_tokens: int) -> Decimal:
    from app.comparison_budget import call_ceiling

    ceiling = call_ceiling(
        input_ceiling=input_tokens,
        max_output_tokens=QUALITY['max_output_tokens'],
        host='https://api.openai.com/v1',
        service_tier=COMPARISON_SERVICE_TIER,
        detail=QUALITY['image_detail'],
        image_count=1,
        text_byte_ceiling_accepted=True,
    )
    if not ceiling['established']:
        raise RuntimeError(ceiling['reason'])
    return Decimal(ceiling['usd'])


def json_safe(value):
    """Plain JSON. Schema classes become names and JSON schema, never class objects."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, type):
        described = {'schema_name': value.__name__, 'schema_module': value.__module__}
        schema = getattr(value, 'model_json_schema', None)
        if schema is not None:
            described['json_schema'] = json_safe(schema())
        return described
    dump = getattr(value, 'model_dump', None)
    if dump is not None and not isinstance(value, type):
        return json_safe(dump(mode='json', by_alias=True))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {'unserializable_type': type(value).__name__}


def _public_request(request: dict) -> dict:
    copied = {key: json_safe(value) for key, value in request.items() if key != 'input'}
    copied['input_roles'] = [item.get('role') for item in request.get('input', []) if isinstance(item, dict)]
    return copied


def request_identity(step: dict) -> str:
    return f"{step['order']}:{step['arm']}:{step['source_filename']}"


def input_fingerprint(request: dict) -> str:
    payload = json.dumps(json_safe(request), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class EvidenceLog:
    """Append-only call records and an atomically replaced summary."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.events_path = self.folder / 'cache_comparison_events.jsonl'
        self.summary_path = self.folder / 'cache_comparison_report.json'

    def append(self, record: dict) -> None:
        line = json.dumps(json_safe(record), ensure_ascii=False) + '\n'
        with self.events_path.open('a', encoding='utf-8') as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def try_append(self, record: dict) -> bool:
        try:
            self.append(record)
        except OSError:
            return False
        return True

    def replace_summary(self, report: dict) -> None:
        payload = json.dumps(json_safe(report), indent=2)
        temporary = self.summary_path.with_name(self.summary_path.name + '.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.summary_path)

    def read_events(self) -> list[dict]:
        if not self.events_path.is_file():
            return []
        rows = []
        for line in self.events_path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows


def requested_identities(events: list[dict]) -> set[str]:
    return {row['identity'] for row in events if row.get('event') == 'request' and row.get('identity')}


def dispatch_recorded_calls(log: EvidenceLog, planned: list[dict], call) -> list[str]:
    """Send only identities that have no saved request. A write failure stops the rest."""
    already = requested_identities(log.read_events())
    sent = []
    for item in planned:
        if item['identity'] in already:
            continue
        if not log.try_append({'event': 'request', **item}):
            break
        call(item)
        sent.append(item['identity'])
        if not log.try_append({'event': 'response', 'identity': item['identity'], 'response_id': item.get('response_id', item['identity'])}):
            break
    return sent


def _assess_quality(parsed, expected: dict, filename: str) -> dict:
    assets = list(getattr(parsed, 'assets', []) or [])
    records = list(getattr(parsed, 'records', []) or [])
    matches = [asset for asset in assets if asset.digital_asset == filename]
    extracted = {'filename_count': len(matches), 'record_kind': None, 'template': None, 'action_needed': None, 'notes': ''}
    problems = []
    asset = matches[0] if len(matches) == 1 else None
    if len(matches) != 1:
        problems.append(f'filename echoed {len(matches)} times')
    record = None
    if asset is not None:
        extracted['action_needed'] = asset.action_needed
        extracted['notes'] = ' '.join(filter(None, [asset.qc_note, asset.task_note, asset.task_scope]))
        record = next((item for item in records if item.proposed_record_id == asset.assigned_record_id), None)
    if record is None:
        problems.append('no record owns the filename')
    else:
        extracted['record_kind'] = record.record_kind
        extracted['template'] = record.template
        extracted['notes'] = (extracted['notes'] + ' ' + ' '.join(filter(None, [record.review_note, record.notes]))).strip()
    if expected.get('record_kind') and extracted['record_kind'] != expected['record_kind']:
        problems.append('record_kind')
    if expected.get('template') and extracted['template'] != expected['template']:
        problems.append('template')
    action = extracted['action_needed'] or ''
    if expected.get('production_tasks') == 'none' and action not in {'', 'none', 'No Adjustment', 'N/A'}:
        problems.append('production task was present')
    if expected.get('action') == 'Archival Border Crop' and action != 'Archival Border Crop':
        problems.append('action')
    if expected.get('action') == 'Rotate + Archival Border Crop' and 'Rotate' not in action:
        problems.append('action')
    if expected.get('action') == 'Rotate + Archival Border Crop' and 'Archival Border Crop' not in action and 'Archival Border Crop' not in extracted['notes']:
        problems.append('action')
    for phrase in expected.get('note_must_include') or []:
        if phrase not in extracted['notes']:
            problems.append(f'note missing {phrase}')
    return {'pass': not problems, 'problems': problems, 'extracted': extracted}


def _arm_range(steps: list[dict], arm: str, ceilings: list[Decimal]) -> dict:
    estimates = []
    bounds = []
    for step, ceiling in zip(steps, ceilings):
        if step['arm'] != arm or not step['included_in_arm_total']:
            continue
        observation = step['observation']
        if observation.get('response_id'):
            estimates.append(observation.get('application_estimate_usd'))
            bounds.append(observation.get('conservative_bound_usd'))
        elif observation.get('error'):
            estimates.append(None)
            bounds.append(ceiling)
    known_estimate = None if not estimates or any(item is None for item in estimates) else sum(estimates, Decimal('0'))
    known_bound = None if not bounds or any(item is None for item in bounds) else sum(bounds, Decimal('0'))
    return {'application_estimate_usd': known_estimate, 'conservative_bound_usd': known_bound}


def _savings_label(optimized: dict, baseline: dict) -> str:
    if savings_are_inconclusive(optimized, baseline):
        return 'inconclusive'
    if optimized['conservative_bound_usd'] < baseline['application_estimate_usd']:
        return 'supported'
    if optimized['application_estimate_usd'] > baseline['conservative_bound_usd']:
        return 'worse'
    return 'inconclusive'


def run_authorized() -> dict:
    """Count, reserve, then generate. This does not write a client register."""
    from openai import OpenAI

    fingerprint = _working_tree_fingerprint()
    instruction, settings = _governing_instruction()
    calibration_path = Path(__file__).resolve().parents[1] / 'project_references' / 'tri-delta-chapters-box31-f04-calibration.json'
    calibration = json.loads(calibration_path.read_text(encoding='utf-8'))
    expectations = load_fixture_expectations(calibration)
    verified = verify_prepared_images()
    image_parts = {}
    for item in verified:
        source = _Named(item['source_filename'])
        data = Path(item['path']).read_bytes()
        image_parts[item['source_filename']] = inline_image_parts([source], [(source, data, 'image/jpeg')])
    run_id = new_comparison_run_id()
    steps = comparison_sequence(instruction, calibration, image_parts, run_id=run_id)
    expected_by_name = {item['source_filename']: item['expected'] for item in expectations['images']}
    report = {
        'build': fingerprint,
        'run_id': run_id,
        'staging_commit_unchanged': STAGING_COMMIT,
        'isolation_method': 'prompt_cache_key per arm under fresh run id',
        'prompt_cache_keys': {arm: arm_prompt_cache_key(run_id, arm) for arm in ARM_ORDER},
        'pricing_inputs': COMPARISON_APPLICATION_ESTIMATE_RATES,
        'acceptance_rule': ACCEPTANCE_RULE,
        'failed_26_oct_run': FAILED_26_OCT_RUN,
        'prior_contaminated_run': PRIOR_CONTAMINATED_RUN,
        'incomplete_clean_run': INCOMPLETE_CLEAN_RUN,
        'images': verified,
        'arm_order': [step['arm'] for step in steps],
        'count_charges_documented': False,
        'generation_calls_sent': 0,
        'generation_calls_with_usage': 0,
        'stop_reason': '',
        'writes_registers': False,
        'steps': [],
    }
    client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=settings.openai_timeout_seconds)
    log = EvidenceLog(evidence_folder_for_run(run_id))
    if log.read_events():
        report['stop_reason'] = 'Clean-run evidence folder was not empty. No provider call was made.'
        _finish_report(log, report, steps, [])
        return report
    already: set[str] = set()
    prior_events: list[dict] = []
    paired: list[tuple[dict, Decimal]] = []
    try:
        for step in steps:
            identity = 'count:' + request_identity(step)
            if identity in already:
                saved = next((row for row in prior_events if row.get('event') == 'response' and row.get('identity') == identity), None)
                if not saved or saved.get('reservation_usd') in (None, ''):
                    report['stop_reason'] = 'A previous count has no saved reservation. No generation call was made.'
                    _finish_report(log, report, steps, [])
                    return report
                step['input_tokens'] = saved.get('input_tokens')
                paired.append((step, Decimal(saved['reservation_usd'])))
                continue
            if not log.try_append({
                'event': 'request',
                'kind': 'input_count',
                'identity': identity,
                'arm': step['arm'],
                'run_id': run_id,
                'prompt_cache_key': step['prompt_cache_key'],
                'input_fingerprint': input_fingerprint(step['request']),
                'reservation_usd': None,
            }):
                report['stop_reason'] = 'Evidence could not be saved. No further provider call was made.'
                _finish_report(log, report, steps, [item[1] for item in paired])
                return report
            step['input_tokens'] = _count_input_tokens(client, step['request'])
            ceiling = _generation_ceiling(step['input_tokens'])
            paired.append((step, ceiling))
            if not log.try_append({
                'event': 'response',
                'kind': 'input_count',
                'identity': identity,
                'input_tokens': step['input_tokens'],
                'reservation_usd': str(ceiling),
            }):
                report['stop_reason'] = 'Count evidence could not be saved. No further provider call was made.'
                _finish_report(log, report, steps, [item[1] for item in paired])
                return report
    except Exception as exc:
        report['stop_reason'] = 'Token count failed before generation. ' + _scrub(str(exc))
        _finish_report(log, report, steps, [item[1] for item in paired])
        return report
    ceilings = [item[1] for item in paired]
    if not reservations_allow_generation(ceilings):
        report['stop_reason'] = 'Counted-request reservations exceed $20 or are missing. No generation call was made.'
        report['reserved_usd'] = str(sum(ceilings, Decimal('0')))
        _finish_report(log, report, steps, ceilings)
        return report
    harness = ComparisonHarness()
    for step, ceiling in paired:
        identity = request_identity(step)
        if identity in already:
            continue
        if not harness.reserve(ceiling):
            report['stop_reason'] = harness.stopped
            break
        if not log.try_append({
            'event': 'request',
            'kind': 'generation',
            'identity': identity,
            'arm': step['arm'],
            'run_id': run_id,
            'prompt_cache_key': step['prompt_cache_key'],
            'input_fingerprint': input_fingerprint(step['request']),
            'reservation_usd': str(ceiling),
        }):
            report['stop_reason'] = 'Evidence could not be saved. No further provider call was made.'
            break
        try:
            assert_prompt_cache_key_allowed(step['request'])
        except RuntimeError as exc:
            report['stop_reason'] = str(exc)
            break
        started = datetime.now(timezone.utc).isoformat()
        try:
            response = client.responses.parse(**step['request'])
        except Exception as exc:
            harness.note_unknown()
            step['observation']['error'] = _scrub(f'{type(exc).__name__}: {exc}')
            step['observation']['started_at'] = started
            step['observation']['completed_at'] = datetime.now(timezone.utc).isoformat()
            step['observation']['status'] = 'Failed'
            step['observation']['failure_reason'] = step['observation']['error']
            step['observation']['billed'] = 'unknown'
            report['generation_calls_sent'] += 1
            report['stop_reason'] = harness.stopped
            if not log.try_append({'event': 'response', 'kind': 'generation', 'identity': identity, 'error': step['observation']['error'], 'started_at': started, 'completed_at': step['observation']['completed_at'], 'billed': 'unknown'}):
                report['stop_reason'] = 'Failure evidence could not be saved. No further provider call was made.'
            break
        report['generation_calls_sent'] += 1
        completed = datetime.now(timezone.utc).isoformat()
        usage = getattr(response, 'usage', None)
        details = getattr(usage, 'input_tokens_details', None)
        detail_dump = details.model_dump() if hasattr(details, 'model_dump') else {}
        parsed = getattr(response, 'output_parsed', None)
        if not log.try_append({
            'event': 'response',
            'kind': 'generation',
            'identity': identity,
            'response_id': getattr(response, 'id', None),
            'service_tier': getattr(response, 'service_tier', None),
            'usage': usage,
            'output': parsed,
            'started_at': started,
            'completed_at': completed,
            'prompt_cache_key': step['prompt_cache_key'],
        }):
            report['stop_reason'] = 'Response evidence could not be saved. No further provider call was made.'
            break
        if usage is None or getattr(usage, 'input_tokens', None) is None or getattr(usage, 'output_tokens', None) is None or 'cached_tokens' not in detail_dump or 'cache_write_tokens' not in detail_dump:
            harness.note_unknown()
            step['observation']['error'] = 'missing usage'
            step['observation']['started_at'] = started
            step['observation']['completed_at'] = completed
            step['observation']['response_id'] = getattr(response, 'id', None)
            step['observation']['service_tier'] = getattr(response, 'service_tier', None)
            step['observation']['status'] = 'Usage Incomplete'
            step['observation']['failure_reason'] = 'missing usage'
            step['observation']['billed'] = 'unknown'
            report['stop_reason'] = harness.stopped
            break
        record_observation(step, response, started, completed)
        report['generation_calls_with_usage'] += 1
        if parsed is None:
            harness.note_unknown()
            step['observation']['error'] = 'missing parsed output'
            report['stop_reason'] = harness.stopped
            break
        step['quality'] = _assess_quality(parsed, expected_by_name[step['source_filename']], step['source_filename'])
    report['reserved_usd'] = str(harness.reserved)
    if not report['stop_reason']:
        report['stop_reason'] = harness.stopped
    optimized = _arm_range(steps, 'optimized', ceilings)
    baseline = _arm_range(steps, 'baseline', ceilings)
    report['optimized'] = optimized
    report['baseline'] = baseline
    report['savings'] = _savings_label(optimized, baseline)
    report['acceptance'] = evaluate_clean_acceptance({
        'steps': [
            {
                **step,
                'observation': step['observation'],
                'quality': step.get('quality'),
            }
            for step in steps
        ],
        'optimized': optimized,
        'baseline': baseline,
    })
    report['regressions'] = [
        {'arm': step['arm'], 'source_filename': step['source_filename'], 'problems': step['quality']['problems']}
        for step in steps if step.get('quality') and not step['quality']['pass']
    ]
    _finish_report(log, report, steps, ceilings)
    return report


def _finish_report(log: EvidenceLog, report: dict, steps: list[dict], ceilings: list[Decimal]) -> None:
    slim_steps = []
    for index, step in enumerate(steps):
        slim_steps.append({
            'order': step['order'],
            'arm': step['arm'],
            'warm_up': step['warm_up'],
            'included_in_arm_total': step['included_in_arm_total'],
            'source_filename': step['source_filename'],
            'governing_fingerprint': step['governing_fingerprint'],
            'run_id': step.get('run_id'),
            'prompt_cache_key': step.get('prompt_cache_key') or step['request'].get('prompt_cache_key'),
            'input_tokens': step.get('input_tokens'),
            'reservation_usd': str(ceilings[index]) if index < len(ceilings) else None,
            'service_tier_sent': step['request'].get('service_tier'),
            'request': _public_request(step['request']),
            'observation': step['observation'],
            'quality': step.get('quality'),
        })
    report['steps'] = slim_steps
    report['events_path'] = str(log.events_path)
    if 'acceptance' not in report:
        report['acceptance'] = evaluate_clean_acceptance({
            'steps': slim_steps,
            'optimized': report.get('optimized') or {},
            'baseline': report.get('baseline') or {},
        })
    report['warm_up_contamination'] = [
        {
            'arm': step['arm'],
            'contamination': step['observation'].get('contamination'),
            'service_tier': step['observation'].get('service_tier'),
            'cache_state': step['observation'].get('cache_state'),
            'prompt_cache_key': step.get('prompt_cache_key'),
        }
        for step in slim_steps if step['warm_up']
    ]
    try:
        log.replace_summary(report)
    except Exception as exc:
        report['summary_error'] = _scrub(f'{type(exc).__name__}: {exc}')
        return
    report['report_path'] = str(log.summary_path)
    print('report_path=' + str(log.summary_path), flush=True)


def main(argv: list[str] | None = None) -> int:
    """`--allow-paid-calls` runs this harness only. It does not change a normal job."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if '--allow-paid-calls' not in args:
        raise SystemExit('Paid calls are paused. Token counts and generation were not run.')
    report = run_authorized()
    print('stop_reason=' + report.get('stop_reason', ''), flush=True)
    print('savings=' + str(report.get('savings', '')), flush=True)
    return 0


if __name__ == '__main__':
    main()


def fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'harness' / 'fixtures' / 'disposable_expectations.json'
