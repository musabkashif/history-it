from __future__ import annotations
import base64, json, time
from pathlib import Path
from app.credits import as_credit_error
from app.diagnostics import Diagnostics, exception_chain
from app.images import JPEG_QUALITY, ORIGINAL_MAX_BYTES, ORIGINAL_MAX_EDGE, RESIZE_MAX_EDGE, prepare_model_image
from app.decisions import decision_instructions
from app.models import InspectionOutput

OUTPUT_SCHEMA = """Structured output schema only. This text does not decide record boundaries, record types, templates, or metadata readiness. Those judgments come from the client runtime prompt, the governing documents, the authorized images, and any client-approved calibration example in the user payload.

Return Lean Split v2 Asset Staging and Record Staging as the structured object. Do not update a spreadsheet, choose a batch, or calculate cumulative totals. All values must be strings. Inspect every authorized image. Preserve every authorized filename exactly once, in manifest order. Each Assigned Record ID must equal a Proposed Record ID in this response. Record Kind is Normal Odyssey or Metadata Only. Record Operation is Create New or Update Existing Open Record. Copy context.prior_open_item from the payload when continuing that record; do not replace a None value and do not create a second record for it. Copy context.folder_code exactly.

When Boundary State After Record starts with Open, or Metadata Readiness is Review, write the visual evidence for that decision in Review Note. Leave Review Note blank only when the record is neither Review nor Open. A folder boundary does not by itself close an item."""

CONNECTION_ATTEMPTS = 4

def _is_connection_error(exc):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names = {cls.__name__ for cls in type(current).__mro__}
        if 'APITimeoutError' not in names and names & {
            'APIConnectionError', 'ConnectError', 'ReadError', 'WriteError',
            'RemoteProtocolError', 'ConnectionError', 'ConnectionResetError',
            'gaierror', 'TimeoutError',
        }:
            return True
        current = current.__cause__
        if current is None and not exc.__suppress_context__:
            current = getattr(exc, '__context__', None)
            exc = current
    return False


def provider_server_error(exc) -> bool:
    """True when the provider failed the request and a smaller request may succeed."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        status = getattr(current, 'status_code', None)
        if status in (500, 502, 503):
            return True
        names = {cls.__name__ for cls in type(current).__mro__}
        if 'InternalServerError' in names:
            return True
        nxt = current.__cause__
        if nxt is None and not current.__suppress_context__:
            nxt = current.__context__
        current = nxt
    return False


def transient_inspection_failure(note: str) -> bool:
    """Network drops and provider HTTP 500s can be retried. They are not content failures."""
    text = (note or '').lower()
    return (
        'getaddrinfo failed' in text
        or 'connection error' in text
        or 'error code: 500' in text
        or 'server had an error processing your request' in text
        or text.strip() == 'inspection retry limit reached'
    )


def inspection_attempt_blocked(attempt: int, max_retries: int, note: str) -> bool:
    """Content failures stop at the cap. A transient failure stays retryable."""
    if attempt <= max_retries:
        return False
    return not transient_inspection_failure(note)


def _still_open(record) -> bool:
    boundary = (record.boundary_state or '').casefold()
    readiness = (record.metadata_readiness or '').casefold()
    return boundary.startswith('open') or readiness == 'review'


def continuation_context(context, output):
    """Hand the open record at the end of one piece to the next piece of the same batch."""
    copied = dict(context or {})
    if not output.assets:
        return copied
    last_id = output.assets[-1].assigned_record_id
    match = next((record for record in output.records if record.proposed_record_id == last_id and _still_open(record)), None)
    if match is None:
        return copied
    copied['prior_open_item'] = last_id
    copied['prior_open_item_source'] = 'Previous images in this batch'
    detail = [match.model_dump(by_alias=True)]
    copied['existing_open_record'] = detail
    copied['prior_open_item_context'] = detail
    return copied


def merge_inspections(parts, continuation_ids=()):
    """Keep manifest order. A continued record stays one row. Other id clashes stay distinct."""
    continued = {item for item in continuation_ids if item and item != 'None'}
    assets = []
    records = []
    seen = set()
    for part_index, output in enumerate(parts):
        remap = {}
        for record in output.records:
            local = record.proposed_record_id
            if local in seen and local in continued:
                records = [item for item in records if item.proposed_record_id != local]
                records.append(record)
                continue
            if local in seen:
                number = 1
                renamed = f'{local}~{number}'
                while renamed in seen:
                    number += 1
                    renamed = f'{local}~{number}'
                remap[local] = renamed
                record.proposed_record_id = renamed
                local = renamed
            seen.add(local)
            records.append(record)
        for asset in output.assets:
            if asset.assigned_record_id in remap:
                asset.assigned_record_id = remap[asset.assigned_record_id]
            assets.append(asset)
    return InspectionOutput(assets=assets, records=records)


def merge_usage(parts):
    combined = {
        'input_tokens': 0,
        'cached_input_tokens': 0,
        'output_tokens': 0,
        'elapsed_seconds': 0,
        'estimated_cost_usd': 0,
        'response_id': '',
    }
    ids = []
    for part in parts:
        for key in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
            combined[key] += int(part.get(key) or 0)
        combined['elapsed_seconds'] += float(part.get('elapsed_seconds') or 0)
        combined['estimated_cost_usd'] += float(part.get('estimated_cost_usd') or 0)
        if part.get('response_id'):
            ids.append(str(part['response_id']))
    combined['elapsed_seconds'] = round(combined['elapsed_seconds'], 2)
    combined['response_id'] = ','.join(ids)
    return combined


def inspect_resilient(inspect_once, sources, context, on_split=None):
    """Inspect the authorized images. A provider HTTP 500 is retried in smaller pieces."""
    try:
        return inspect_once(sources, context)
    except Exception as exc:
        converted = as_credit_error(exc)
        if converted is not exc:
            raise converted from exc
        if not provider_server_error(exc) or len(sources) < 2:
            if provider_server_error(exc) and len(sources) == 1:
                filename = getattr(sources[0], 'name', 'this image')
                raise RuntimeError(f'OpenAI rejected {filename} with a server error. The other images in the batch can be inspected separately.') from exc
            raise
        if on_split is not None:
            on_split(exc, sources)
        midpoint = len(sources) // 2
        left_output, left_usage = inspect_resilient(inspect_once, sources[:midpoint], context, on_split)
        right_context = continuation_context(context, left_output)
        right_output, right_usage = inspect_resilient(inspect_once, sources[midpoint:], right_context, on_split)
        continued = right_context.get('prior_open_item')
        return merge_inspections([left_output, right_output], [continued]), merge_usage([left_usage, right_usage])

def _runtime_prompt(settings):
    configured = getattr(settings, 'runtime_prompt_path', None) or ''
    candidates = []
    if configured:
        candidates.append(Path(configured))
    app_dir = Path(__file__).resolve().parents[1]
    candidates.append(app_dir.parent / 'Runtime_prompt_old_chatgpt.md')
    candidates.append(app_dir / 'prompts' / 'Runtime_prompt_old_chatgpt.md')
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding='utf-8'), path.name
    raise ValueError('Canonical runtime prompt is missing. Set RUNTIME_PROMPT_PATH to Runtime_prompt_old_chatgpt.md')

def _docx_text(path):
    from docx import Document
    document = Document(path)
    parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for index, table in enumerate(document.tables, 1):
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
            if any(cells):
                rows.append(' | '.join(cells))
        if rows:
            parts.append(f'Table {index}\n' + '\n'.join(rows))
    return '\n'.join(parts)

def _estimate_cost(settings, inp, cached, out):
    in_rate = float(getattr(settings, 'openai_input_usd_per_million', 0) or 0)
    cached_rate = float(getattr(settings, 'openai_cached_input_usd_per_million', 0) or 0)
    out_rate = float(getattr(settings, 'openai_output_usd_per_million', 0) or 0)
    return ((inp - cached) * in_rate + cached * cached_rate + out * out_rate) / 1e6

class Inspector:
    def __init__(self, settings, drive, client=None):
        self.settings = settings
        self.drive = drive
        self.diagnostics = Diagnostics(getattr(settings, 'diagnostics_dir', './logs'))
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=getattr(settings, 'openai_timeout_seconds', 900))
        self.client = client
        self._sleep = time.sleep

    def _call(self, job, batch, attempt, operation, func):
        delay = 2
        for try_index in range(1, CONNECTION_ATTEMPTS + 1):
            try:
                return func()
            except Exception as exc:
                if not _is_connection_error(exc) or try_index == CONNECTION_ATTEMPTS:
                    raise
                self.diagnostics.write(
                    job, batch, attempt, 'connection_retry',
                    operation=operation, try_index=try_index, attempts=CONNECTION_ATTEMPTS,
                    error_type=type(exc).__name__, error=str(exc), error_chain=exception_chain(exc),
                )
                self._sleep(delay)
                delay = min(delay * 2, 16)

    def _image_content(self, job, batch, attempt, blobs, file_ids):
        files_api = getattr(getattr(self.client, 'files', None), 'create', None)
        content = []
        for src, data, mime in blobs:
            content.append({'type': 'input_text', 'text': f'Source filename: {src.name}; Drive ID: {src.id}'})
            if files_api is None:
                content.append({
                    'type': 'input_image',
                    'image_url': f'data:{mime};base64,{base64.b64encode(data).decode()}',
                    'detail': 'high',
                })
                continue
            uploaded = self._call(
                job, batch, attempt, f'files.create {src.name}',
                lambda data=data, mime=mime, name=src.name: self.client.files.create(
                    file=(name, data, mime),
                    purpose='user_data',
                    expires_after={'anchor': 'created_at', 'seconds': 3600},
                ),
            )
            file_id = getattr(uploaded, 'id', '') or ''
            if not file_id:
                raise ValueError(f'Image upload did not return a file id for {src.name}')
            file_ids.append(file_id)
            content.append({'type': 'input_image', 'file_id': file_id, 'detail': 'high'})
        return content

    def _delete_files(self, file_ids):
        delete = getattr(getattr(self.client, 'files', None), 'delete', None)
        if delete is None:
            return
        for file_id in file_ids:
            try:
                delete(file_id)
            except Exception:
                pass

    def inspect(self, job, batch, sources, context, rules):
        def once(chosen, chosen_context):
            return self._inspect_once(job, batch, chosen, chosen_context, rules)

        def on_split(exc, chosen):
            try:
                self.diagnostics.write(
                    job, batch, int((context or {}).get('inspection_attempt') or 0), 'provider_split',
                    error_type=type(exc).__name__, error=str(exc)[:500],
                    source_image_count=len(chosen),
                    filenames=[getattr(source, 'name', '') for source in chosen],
                )
            except Exception:
                pass

        return inspect_resilient(once, sources, context, on_split)

    def _inspect_once(self, job, batch, sources, context, rules):
        prompt_text, prompt_name = _runtime_prompt(self.settings)
        docs = Path(self.settings.governing_docs_dir)
        references = []
        doc_names = []
        for path in sorted(docs.iterdir()):
            if path.suffix.lower() in {'.md', '.txt'}:
                references.append(path.name + '\n' + path.read_text(encoding='utf-8'))
                doc_names.append(path.name)
            elif path.suffix.lower() == '.docx':
                references.append(path.name + '\n' + _docx_text(path))
                doc_names.append(path.name)
        if not references:
            raise ValueError('No readable governing documents')
        approved = context.get('approved_project_reference') if isinstance(context, dict) else None
        reference_id = ''
        reference_version = ''
        if isinstance(approved, dict):
            reference_id = approved.get('reference_id') or approved.get('source_file') or ''
            reference_version = str(approved.get('version') or '')
        blobs = []
        modes = []
        attempt = int(getattr(context, 'attempt', 0) or 0) if not isinstance(context, dict) else int(context.get('inspection_attempt', 0) or 0)
        start = time.monotonic()
        original_max_bytes = int(getattr(self.settings, 'image_original_max_bytes', ORIGINAL_MAX_BYTES) or ORIGINAL_MAX_BYTES)
        original_max_edge = int(getattr(self.settings, 'image_original_max_edge', ORIGINAL_MAX_EDGE) or ORIGINAL_MAX_EDGE)
        resize_max_edge = int(getattr(self.settings, 'image_resize_max_edge', RESIZE_MAX_EDGE) or RESIZE_MAX_EDGE)
        jpeg_quality = int(getattr(self.settings, 'image_jpeg_quality', JPEG_QUALITY) or JPEG_QUALITY)
        try:
            for src in sources:
                raw = self._call(job, batch, attempt, f'drive.image {src.name}', lambda src=src: self.drive.image(src))
                if hasattr(raw, 'read'):
                    raw = raw.read()
                source_bytes = len(raw) if isinstance(raw, (bytes, bytearray)) else None
                data, mime, mode = prepare_model_image(
                    raw,
                    getattr(src, 'mime_type', '') or 'image/jpeg',
                    original_max_bytes=original_max_bytes,
                    original_max_edge=original_max_edge,
                    resize_max_edge=resize_max_edge,
                    jpeg_quality=jpeg_quality,
                )
                modes.append({'filename': src.name, 'mode': mode, 'mime': mime, 'source_bytes': source_bytes, 'prepared_bytes': len(data)})
                blobs.append((src, data, mime))
        except Exception as exc:
            setattr(exc, 'elapsed_seconds', round(time.monotonic() - start, 2))
            try:
                path = self.diagnostics.write(
                    job, batch, attempt, 'inspection_error', error_type=type(exc).__name__, error=str(exc),
                    error_chain=exception_chain(exc),
                    elapsed_seconds=getattr(exc, 'elapsed_seconds', 0), success=False, image_preparation=modes,
                    image_count=len(modes),
                    source_bytes_total=sum(item.get('source_bytes') or 0 for item in modes),
                    prepared_bytes_total=sum(item.get('prepared_bytes') or 0 for item in modes),
                )
                setattr(exc, 'diagnostics_path', str(path))
            except Exception:
                pass
            raise
        instruction = (
            'The client runtime prompt below is the canonical inspection workflow. '
            'Return Lean Split v2 Asset Staging and Record Staging in the structured object. '
            'Do not update Google Sheets. '
            'A client-approved calibration example in the user payload is calibration for the named project and box, not a filename rule and not an answer-key extract. '
            'Inspect every authorized image. Do not copy an example when the images disagree, and do not close an item only because its folder has ended.\n\n'
            + prompt_text
            + '\n\n'
            + '\n\n'.join(references)
            + '\n\n'
            + OUTPUT_SCHEMA
            + '\n\n'
            + decision_instructions()
        )
        prior = context.get('prior_open_item') if isinstance(context, dict) else None
        prior_text = 'None' if prior in (None, '', 'None') else str(prior)
        existing = []
        if isinstance(context, dict) and prior_text != 'None':
            existing = context.get('existing_open_record') or context.get('prior_open_item_context') or []
        payload = {
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
            'authorized_filenames': [s.name for s in sources],
        }
        start = time.monotonic()
        response_meta = {}
        file_ids = []
        attempt = int(getattr(context, 'attempt', 0) or 0) if not isinstance(context, dict) else int(context.get('inspection_attempt', 0) or 0)
        max_tokens = getattr(self.settings, 'openai_max_output_tokens', 0)
        effort = (getattr(self.settings, 'openai_reasoning_effort', '') or '').strip()
        prompt_ids = [prompt_name, *doc_names]
        if reference_id:
            prompt_ids.append(reference_id)
        try:
            images = self._image_content(job, batch, attempt, blobs, file_ids)
            image_transport = 'openai_files' if file_ids else 'inline'
            self.diagnostics.write(
                job, batch, attempt, 'request_prepared',
                model=self.settings.openai_model,
                reasoning_effort=effort,
                max_output_tokens=max_tokens,
                source_image_count=len(sources),
                authorized_filenames=[s.name for s in sources],
                prior_open_item=prior_text,
                prior_open_item_source=payload['prior_open_item_source'],
                prompt_identifiers=prompt_ids,
                reference_id=reference_id,
                reference_version=reference_version,
                image_preparation=modes,
                image_count=len(modes),
                image_transport=image_transport,
                uploaded_file_count=len(file_ids),
                source_bytes_total=sum(item.get('source_bytes') or 0 for item in modes),
                prepared_bytes_total=sum(item.get('prepared_bytes') or 0 for item in modes),
                prompt_character_count=len(instruction),
                rules_count=len(rules),
            )
            request = {
                'model': self.settings.openai_model,
                'input': [
                    {'role': 'system', 'content': instruction},
                    {'role': 'user', 'content': [{'type': 'input_text', 'text': json.dumps(payload)}] + images},
                ],
                'text_format': InspectionOutput,
            }
            if max_tokens:
                request['max_output_tokens'] = max_tokens
            if effort:
                request['reasoning'] = {'effort': effort}
            response = self._call(job, batch, attempt, 'responses.parse', lambda: self.client.responses.parse(**request))
            usage = getattr(response, 'usage', None)
            details = getattr(usage, 'input_tokens_details', None)
            response_meta = {
                'response_id': getattr(response, 'id', ''),
                'status': getattr(response, 'status', ''),
                'incomplete_details': getattr(response, 'incomplete_details', None),
                'input_tokens': getattr(usage, 'input_tokens', 0) if usage else 0,
                'cached_input_tokens': getattr(details, 'cached_tokens', 0) if details else 0,
                'output_tokens': getattr(usage, 'output_tokens', 0) if usage else 0,
            }
            response_meta['estimated_cost_usd'] = _estimate_cost(
                self.settings,
                int(response_meta['input_tokens'] or 0),
                int(response_meta['cached_input_tokens'] or 0),
                int(response_meta['output_tokens'] or 0),
            )
            output = response.output_parsed
            returned_assets = [a.digital_asset for a in output.assets] if output else []
            returned_records = [r.proposed_record_id for r in output.records] if output else []
            self.diagnostics.write(
                job, batch, attempt, 'response_received', **response_meta,
                returned_asset_filenames=returned_assets,
                returned_record_ids=returned_records,
            )
            if output is None:
                raise ValueError('Missing structured output')
            expected = [s.name for s in sources]
            actual = [a.digital_asset for a in output.assets]
            if actual != expected:
                missing = sorted(set(expected) - set(actual))
                extra = sorted(set(actual) - set(expected))
                self.diagnostics.write(
                    job, batch, attempt, 'inspection_validation_failed',
                    reason='filename_integrity', expected_count=len(expected),
                    returned_count=len(actual), missing=missing, extra=extra,
                    expected_order=expected, returned_order=actual,
                )
                raise ValueError(
                    f'Inspection returned incomplete/non-ordered staging: expected {len(expected)} assets, got {len(actual)}; missing={missing[:5]}; extra={extra[:5]}'
                )
            if len(output.records) == 0:
                self.diagnostics.write(job, batch, attempt, 'inspection_validation_failed', reason='no_record_rows')
                raise ValueError('Inspection returned no Record Staging rows')
            linked = {a.assigned_record_id for a in output.assets}
            proposed = {r.proposed_record_id for r in output.records}
            if not all(a.assigned_record_id for a in output.assets) or not linked.issubset(proposed):
                self.diagnostics.write(
                    job, batch, attempt, 'inspection_validation_failed', reason='unlinked_assets',
                    linked_record_ids=sorted(linked), returned_record_ids=sorted(proposed),
                )
                raise ValueError('Every inspected asset must link to a returned Record Staging row')
            elapsed = round(time.monotonic() - start, 2)
            self.diagnostics.write(
                job, batch, attempt, 'inspection_validated',
                elapsed_seconds=elapsed,
                success=True,
                image_preparation=modes,
                estimated_cost_usd=response_meta['estimated_cost_usd'],
                response_id=response_meta['response_id'],
            )
            usage_out = {
                'input_tokens': response_meta['input_tokens'],
                'cached_input_tokens': response_meta['cached_input_tokens'] or 0,
                'output_tokens': response_meta['output_tokens'],
                'elapsed_seconds': elapsed,
                'response_id': response_meta['response_id'],
                'estimated_cost_usd': response_meta['estimated_cost_usd'],
            }
            return output, usage_out
        except Exception as e:
            setattr(e, 'elapsed_seconds', round(time.monotonic() - start, 2))
            if response_meta:
                setattr(e, 'inspection_usage', {
                    'input_tokens': response_meta.get('input_tokens') or 0,
                    'cached_input_tokens': response_meta.get('cached_input_tokens') or 0,
                    'output_tokens': response_meta.get('output_tokens') or 0,
                    'response_id': response_meta.get('response_id') or '',
                    'elapsed_seconds': getattr(e, 'elapsed_seconds', 0),
                    'estimated_cost_usd': response_meta.get('estimated_cost_usd') or 0,
                })
            try:
                path = self.diagnostics.write(
                    job, batch, attempt, 'inspection_error',
                    error_type=type(e).__name__,
                    error=str(e),
                    error_chain=exception_chain(e),
                    elapsed_seconds=getattr(e, 'elapsed_seconds', 0),
                    success=False,
                    image_preparation=modes,
                    image_count=len(modes),
                    source_bytes_total=sum(item.get('source_bytes') or 0 for item in modes),
                    prepared_bytes_total=sum(item.get('prepared_bytes') or 0 for item in modes),
                    prior_open_item_source=payload['prior_open_item_source'],
                    prompt_identifiers=prompt_ids,
                    estimated_cost_usd=(response_meta or {}).get('estimated_cost_usd', 0),
                    response_id=(response_meta or {}).get('response_id', ''),
                )
                setattr(e, 'diagnostics_path', str(path))
            except Exception:
                pass
            credit = as_credit_error(e)
            if credit is not e:
                raise credit from e
            raise
        finally:
            self._delete_files(file_ids)
