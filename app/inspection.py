from __future__ import annotations
import base64, json, time
from pathlib import Path
from app.credits import as_credit_error
from app.models import InspectionOutput
from app.diagnostics import Diagnostics

INSPECTION_CONTRACT = """Inspection phase only. Return Lean Split v2 staging rows. Do not update a spreadsheet, choose a batch, or calculate cumulative totals.

The payload context is authoritative. context.prior_open_item None means there is no open item; do not invent one from earlier work. When a prior open item exists, preserve that record id, continue it only when these images are the same intellectual item, and set Record Operation to Update Existing Open Record. Distinguish new filenames from assets already committed. A batch boundary does not close an intellectual item. A folder boundary does not close an item unless the governing workflow and the images support closure. If the item continues past this batch, Boundary State After Record must start with Open.

Visually inspect every authorized image. Do not sample. Apply the governing documents, including their tables. Folder covers, enclosure labels, project identifier sheets, descriptive inventories, contextual packets, wrappers, and similar context-only captures are Metadata Only. Do not turn those captures into Ready Odyssey records or production tasks.

A blank back belongs with the same capture as its front. On a normal Odyssey item, that blank back stays a normal asset of that item. On a folder cover, enclosure label, identifier sheet, descriptive inventory, or other context-only capture, the blank back stays on that same Metadata Only reference. Do not promote that pair into a Ready Odyssey record because the back is blank.

If the last images in this batch are still part of a multi-page item, and this batch does not contain the end of that item, Boundary State After Record must start with Open. The next batch continues it. A batch boundary does not close an intellectual item.

Use Action Needed values from the governing action table exactly. No Adjustment, N/A, and Review are not production tasks. Crop / Recrop, Deskew, Rotate, Split Spread, Publication Crop, Archival Border Crop, Create Derivative Crop, Reshoot, and Additional Imaging Required are production tasks. Production Queue Eligible follows that action.

context.folder_code is the Source Group / Folder and the Processing Unit. Use that folder code exactly. Do not copy a status phrase such as Not Started or Completed.

Assigned Record ID and Proposed Record ID are grouping keys. Assets that belong to one intellectual record share one key, and that key has exactly one Record Staging row. To continue the prior open item, use its exact id. Otherwise use a short local key. The service replaces local keys with the register id. Do not invent a project code.

Record Kind is Normal Odyssey or Metadata Only. Record Operation is Create New or Update Existing Open Record. A Normal Odyssey record needs Metadata Readiness of Ready, Blocked - Cropping/QC, Blocked - Additional Imaging, or Review. Use only controlled values from the rules payload. All values must be strings. Preserve filenames and do not invent a missing image."""

def model_jpeg(data):
    """Smaller JPEG for the model only. Drive bytes are not modified."""
    from io import BytesIO
    from PIL import Image, ImageOps
    with Image.open(BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        out = BytesIO()
        image.save(out, format='JPEG', quality=80, optimize=True)
        return out.getvalue()

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

class Inspector:
    def __init__(self,settings,drive,client=None):
        self.settings=settings;self.drive=drive
        self.diagnostics=Diagnostics(getattr(settings, 'diagnostics_dir', './logs'))
        if client is None:
            from openai import OpenAI
            client=OpenAI(api_key=settings.openai_api_key,max_retries=0,timeout=getattr(settings,'openai_timeout_seconds',900))
        self.client=client

    def inspect(self,job,batch,sources,context,rules):
        docs=Path(self.settings.governing_docs_dir)
        references=[]
        for path in sorted(docs.iterdir()):
            if path.suffix.lower() in {'.md','.txt'}:
                references.append(path.name+'\n'+path.read_text(encoding='utf-8'))
            elif path.suffix.lower()=='.docx':
                references.append(path.name+'\n'+_docx_text(path))
        if not references: raise ValueError('No readable governing documents')
        images=[]
        for src in sources:
            data=model_jpeg(self.drive.image(src))
            images.append({'type':'input_text','text':f'Source filename: {src.name}; Drive ID: {src.id}'})
            images.append({'type':'input_image','image_url':f'data:image/jpeg;base64,{base64.b64encode(data).decode()}'})
        instruction='\n\n'.join(references)+'\n\n'+INSPECTION_CONTRACT
        payload={'job_id':job,'batch_id':batch,'context':context,'rules':rules,'authorized_filenames':[s.name for s in sources]}
        start=time.monotonic()
        response_meta={}
        attempt = int(getattr(context, 'attempt', 0) or 0) if not isinstance(context, dict) else int(context.get('inspection_attempt', 0) or 0)
        # The orchestrator supplies the attempt in context. Keep a useful
        # fallback for direct callers and tests.
        max_tokens=getattr(self.settings,'openai_max_output_tokens',0)
        effort=(getattr(self.settings,'openai_reasoning_effort','') or '').strip()
        self.diagnostics.write(job, batch, attempt, 'request_prepared', model=self.settings.openai_model,
                               reasoning_effort=effort, max_output_tokens=max_tokens,
                               source_image_count=len(sources), authorized_filenames=[s.name for s in sources],
                               prompt_character_count=len(instruction), rules_count=len(rules))
        try:
            request={'model':self.settings.openai_model,'input':[{'role':'system','content':instruction},{'role':'user','content':[{'type':'input_text','text':json.dumps(payload)}]+images}], 'text_format':InspectionOutput}
            if max_tokens: request['max_output_tokens']=max_tokens
            if effort: request['reasoning']={'effort':effort}
            response=self.client.responses.parse(**request)
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
            output=response.output_parsed
            returned_assets = [a.digital_asset for a in output.assets] if output else []
            returned_records = [r.proposed_record_id for r in output.records] if output else []
            self.diagnostics.write(job, batch, attempt, 'response_received', **response_meta,
                                   returned_asset_filenames=returned_assets,
                                   returned_record_ids=returned_records)
            if output is None: raise ValueError('Missing structured output')
            expected=[s.name for s in sources]
            actual=[a.digital_asset for a in output.assets]
            if actual != expected:
                missing=sorted(set(expected)-set(actual)); extra=sorted(set(actual)-set(expected))
                self.diagnostics.write(job, batch, attempt, 'inspection_validation_failed',
                                       reason='filename_integrity', expected_count=len(expected),
                                       returned_count=len(actual), missing=missing, extra=extra,
                                       expected_order=expected, returned_order=actual)
                raise ValueError(f'Inspection returned incomplete/non-ordered staging: expected {len(expected)} assets, got {len(actual)}; missing={missing[:5]}; extra={extra[:5]}')
            if len(output.records)==0:
                self.diagnostics.write(job, batch, attempt, 'inspection_validation_failed', reason='no_record_rows')
                raise ValueError('Inspection returned no Record Staging rows')
            linked={a.assigned_record_id for a in output.assets}
            proposed={r.proposed_record_id for r in output.records}
            if not all(a.assigned_record_id for a in output.assets) or not linked.issubset(proposed):
                self.diagnostics.write(job, batch, attempt, 'inspection_validation_failed', reason='unlinked_assets',
                                       linked_record_ids=sorted(linked), returned_record_ids=sorted(proposed))
                raise ValueError('Every inspected asset must link to a returned Record Staging row')
            self.diagnostics.write(job, batch, attempt, 'inspection_validated', elapsed_seconds=round(time.monotonic()-start, 2))
            return output,{'input_tokens':response_meta['input_tokens'],'cached_input_tokens':response_meta['cached_input_tokens'] or 0,'output_tokens':response_meta['output_tokens'],'elapsed_seconds':round(time.monotonic()-start,2),'response_id':response_meta['response_id']}
        except Exception as e:
            # API failures can be ambiguous. Caller checks staging before another attempt.
            setattr(e,'elapsed_seconds',round(time.monotonic()-start,2))
            if response_meta:
                setattr(e, 'inspection_usage', {
                    'input_tokens': response_meta.get('input_tokens') or 0,
                    'cached_input_tokens': response_meta.get('cached_input_tokens') or 0,
                    'output_tokens': response_meta.get('output_tokens') or 0,
                    'response_id': response_meta.get('response_id') or '',
                    'elapsed_seconds': getattr(e, 'elapsed_seconds', 0),
                })
            try:
                path=self.diagnostics.write(job, batch, attempt, 'inspection_error', error_type=type(e).__name__,
                                            error=str(e), elapsed_seconds=getattr(e, 'elapsed_seconds', 0))
                setattr(e, 'diagnostics_path', str(path))
            except Exception:
                pass
            credit = as_credit_error(e)
            if credit is not e:
                raise credit from e
            raise
