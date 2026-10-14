"""Approved controlled vocabulary from the governing packet.

Capitalization and whitespace may be canonicalized to the approved spelling.
A different decision is not renamed. In particular, Needs Adjustment is not
treated as Action Required: the master action table does not give those words
the same meaning.
"""
from __future__ import annotations

TEMPLATES = frozenset({
    'Artwork', 'Publication', 'Clipping',
    'Correspondence', 'Diary/Journal', 'Ephemera',
    'Document', 'Ledger', 'Legal Document',
    'Map', 'Object', 'Photographic Image',
    'Postcard', 'Scrapbook', 'Scrapbook Page',
    'Sheet Music', 'Technical Drawing', 'Textile',
})

# Established example types. This is not a closed type list. The Odyssey
# reference says type may be more specific than these examples.
EXAMPLE_TYPES = frozenset({
    'Songbook', 'Booklet', 'Magazine', 'Cookbook', 'Program',
    'Photograph', 'Negative', 'Slide', 'Print',
    'Minutes', 'Report', 'Form', 'Convention Program', 'Diary Transcript', 'Convention Notes',
    'Letter', 'Handwritten Note', 'Envelope',
    'Tag/Label', 'Ticket', 'Card/Memento',
    'Scrapbook', 'Scrapbook Page',
    'Biographical Notes', 'Research Notes',
})

# Type evidence that the capture is a collection item, not a context-only sheet.
# Research notes are included because a descriptive inventory is the only
# notes-like capture the packet allows as Metadata Only, and it must say so
# by using the Metadata Only template with no collection type.
COLLECTION_TYPES = frozenset({
    'Letter', 'Handwritten Note', 'Envelope', 'Research Notes', 'Biographical Notes', 'Scrapbook Page', 'Photograph',
    'Newspaper', 'Magazine', 'Yearbook Excerpt', 'Yearbook Page',
})

ACTIONS = frozenset({
    'No Adjustment',
    'N/A',
    'Crop / Recrop',
    'Deskew',
    'Rotate',
    'Split Spread',
    'Split Spread + Publication Crop',
    'Publication Crop',
    'Archival Border Crop',
    'Create Derivative Crop',
    'Crop / Recrop + Create Derivative Crop',
    'Reshoot',
    'Additional Imaging Required',
    'Review',
})

QUEUE_ACTIONS = frozenset({
    'Crop / Recrop',
    'Deskew',
    'Rotate',
    'Split Spread',
    'Split Spread + Publication Crop',
    'Publication Crop',
    'Archival Border Crop',
    'Create Derivative Crop',
    'Crop / Recrop + Create Derivative Crop',
    'Reshoot',
    'Additional Imaging Required',
})

# Pass: nothing to do. Reference Only: a metadata reference.
# Action Required: a real production action is still pending.
QC_STATUSES = frozenset({'Pass', 'Reference Only', 'Action Required'})

CROP_STANDARDS = frozenset({
    'Archival Border',
    'Page Edge',
    'Publication Page Edge',
    'Book Page Edge',
    'Tight Border',
    'Scrapbook Loose Item / Tight Border',
    'Scrapbook Page Edge',
    'N/A',
})

# The scrapbook reference's tight-border rule and the short standard name.
CROP_SYNONYMS = {
    'tight border': 'Tight Border',
    'scrapbook loose item / tight border': 'Tight Border',
}

READINESS = frozenset({
    'Ready',
    'Blocked - Cropping/QC',
    'Blocked - Additional Imaging',
    'Review',
    'Metadata Reference',
})


def collapse(value) -> str:
    return ' '.join(str(value or '').strip().split())


def canonicalize(value, approved: frozenset[str]) -> str | None:
    """Return the approved spelling, or None when the value is not that term."""
    text = collapse(value)
    if not text:
        return ''
    if text in approved:
        return text
    folded = text.casefold()
    matches = [item for item in approved if item.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    return None


def crop_standard_key(value) -> str | None:
    text = collapse(value)
    if not text:
        return ''
    canonical = canonicalize(text, CROP_STANDARDS)
    if canonical is None:
        return None
    return CROP_SYNONYMS.get(canonical.casefold(), canonical)


def _note(record, sentence: str):
    current = collapse(record.review_note)
    if sentence.casefold() in current.casefold():
        return
    record.review_note = f'{current} {sentence}'.strip()


def _collection_conflict(record) -> bool:
    template = canonicalize(record.template, TEMPLATES | {'Metadata Only'}) or collapse(record.template)
    type_name = collapse(record.type)
    type_key = canonicalize(type_name, COLLECTION_TYPES) or type_name
    if record.record_kind != 'Metadata Only':
        return False
    if template in TEMPLATES:
        return True
    return type_key in COLLECTION_TYPES


def _blob(*parts) -> str:
    return ' '.join(collapse(part) for part in parts).casefold()


def _record_blob(record, assets) -> str:
    return _blob(
        record.template, record.type, record.further_detail, record.notes, record.review_note, record.asset_role if hasattr(record, 'asset_role') else '',
        *[f"{asset.asset_role} {asset.qc_note} {asset.task_note} {asset.exception_note}" for asset in assets],
    )


def _append_detail(record, sentence: str):
    current = collapse(record.further_detail)
    if sentence.casefold() in current.casefold():
        return
    record.further_detail = f'{current} {sentence}'.strip()


def _margin_only(note: str) -> bool:
    """A named margin is not a crop unless the note also gives a real defect."""
    text = collapse(note).casefold()
    if not text:
        return False
    if any(word in text for word in (
        'excessive', 'large background', 'substantial', 'most of the frame', 'cut off', 'clipped',
        'split', 'spread', 'two pages', 'two-page', 'skewed', 'crooked',
    )):
        return False
    return any(word in text for word in ('border', 'margin', 'surround', 'background'))


def _is_photographic_print(note: str, record) -> bool:
    text = collapse(note).casefold()
    if 'mounted' in text:
        return False
    if getattr(record, 'template', '') == 'Photographic Image' or getattr(record, 'type', '') in {'Photograph', 'Print'}:
        return True
    return any(phrase in text for phrase in ('photographic print', 'this photograph', 'vintage print'))


def apply_authorized_rules(output):
    """Apply client-authorized archival rules. These are not filename mappings."""
    by_record = {}
    for asset in output.assets:
        by_record.setdefault(asset.assigned_record_id, []).append(asset)
    for record in output.records:
        owned = by_record.get(record.proposed_record_id, [])
        blob = _record_blob(record, owned)
        accounts = any(word in blob for word in ('account book', 'transaction', 'ledger entry', 'debit', 'credit', 'dues paid'))
        biographical = any(word in blob for word in ('biographical', 'personnel', 'profile', 'born ', 'age '))
        notebook = any(word in blob for word in ('notebook', 'note book', 'hole-bound', 'punched notebook', 'bound volume', 'bound notebook'))
        if notebook and biographical and not accounts:
            record.record_kind = 'Normal Odyssey'
            record.template = 'Document'
            record.type = 'Biographical Notes'
            if record.metadata_readiness in {'Review', 'Metadata Reference'}:
                record.metadata_readiness = 'Ready'
            _append_detail(record, 'Notebook binding.')
        elif accounts and notebook:
            record.record_kind = 'Normal Odyssey'
            record.template = 'Ledger'
        research = any(word in blob for word in ('research note', 'research index', 'citation index', 'citation'))
        if research and not any(word in blob for word in ('file folder', 'manila')):
            record.record_kind = 'Normal Odyssey'
            record.template = 'Document'
            record.type = 'Research Notes'
            if record.metadata_readiness in {'Review', 'Metadata Reference'}:
                record.metadata_readiness = 'Ready'
        letter_body = any(word in blob for word in ('letter body', 'letter text', 'signed letter'))
        if 'envelope' in blob and not letter_body:
            record.record_kind = 'Normal Odyssey'
            record.template = 'Correspondence'
            record.type = 'Envelope'
            if record.metadata_readiness in {'Review', 'Metadata Reference'}:
                record.metadata_readiness = 'Ready'
        described = _blob(record.further_detail, record.notes, record.review_note, *[f"{asset.qc_note} {asset.asset_role}" for asset in owned])
        folder = any(word in described for word in ('file folder', 'manila folder', 'blank file folder', 'organizational enclosure'))
        if folder and 'scrapbook' not in described:
            record.record_kind = 'Metadata Only'
            record.template = 'Metadata Only'
            record.type = ''
        publication = record.template == 'Publication' or 'publication' in blob or 'periodical' in blob or 'magazine' in blob
        newspaper = 'newspaper' in blob or 'newsprint' in blob
        if publication and newspaper and 'magazine' not in blob and 'periodical' not in blob:
            record.template = 'Publication'
            record.type = 'Newspaper'
        elif publication and (('magazine' in blob) or ('periodical' in blob) or ('publication issue' in blob)) and 'newspaper' not in blob and 'newsprint' not in blob:
            record.template = 'Publication'
            record.type = 'Magazine'
        bound_book = notebook or (record.template == 'Document' and record.type == 'Biographical Notes') or record.template == 'Ledger'
        record_note = _blob(record.review_note, record.further_detail)
        mounted_on_final = 'mounted photograph' in record_note and 'final' in record_note
        for index, asset in enumerate(owned):
            asset_blob = _blob(asset.asset_role, asset.qc_note, asset.task_note, asset.exception_note)
            mounted = any(phrase in asset_blob for phrase in (
                'mounted photograph', 'mounted photo', 'photograph mounted', 'tipped-in', 'pasted photograph', 'photo pasted',
            )) or (mounted_on_final and index == len(owned) - 1)
            if mounted and asset.action_needed in {'Crop / Recrop', 'Publication Crop', 'Crop', 'No Adjustment', 'Archival Border Crop', ''}:
                asset.action_needed = 'Crop / Recrop + Create Derivative Crop'
                if asset.required_crop_standard in {'', 'Page Edge', 'Publication Page Edge', 'Archival Border'}:
                    asset.required_crop_standard = 'Book Page Edge'
                if collapse(asset.expected_output_asset_count) in {'', '0', '1'}:
                    asset.expected_output_asset_count = '2'
            elif not bound_book and _margin_only(asset_blob) and asset.action_needed in {
                'Archival Border Crop', 'Crop / Recrop', 'Publication Crop', 'Crop',
            }:
                asset.action_needed = 'No Adjustment'
                if record.template == 'Publication':
                    asset.required_crop_standard = 'Publication Page Edge'
                elif record.template == 'Photographic Image' or 'photograph' in asset_blob:
                    asset.required_crop_standard = 'Archival Border'
                elif not bound_book:
                    asset.required_crop_standard = 'Tight Border'
            elif record.template in {'Document', 'Correspondence'} and not bound_book and asset.action_needed == 'Archival Border Crop':
                asset.action_needed = 'Crop / Recrop'
                asset.required_crop_standard = 'Tight Border'
            elif record.template in {'Document', 'Correspondence'} and not bound_book and asset.action_needed == 'No Adjustment' and asset.required_crop_standard == 'Archival Border':
                asset.required_crop_standard = 'Tight Border'
            elif asset.action_needed == 'No Adjustment' and _is_photographic_print(asset_blob, record):
                asset.required_crop_standard = 'Archival Border'
            elif bound_book and not publication:
                if asset.action_needed == 'Publication Crop':
                    asset.action_needed = 'Crop / Recrop'
                if asset.action_needed == 'Crop / Recrop' and asset.required_crop_standard in {'', 'Page Edge', 'Publication Page Edge'}:
                    asset.required_crop_standard = 'Book Page Edge'
    return output


def qc_status_for(action: str, metadata_only: bool) -> str:
    if metadata_only:
        return 'Reference Only'
    if action in QUEUE_ACTIONS:
        return 'Action Required'
    if action in {'No Adjustment', 'N/A', ''}:
        return 'Pass'
    return ''


def unresolved_material_review(record) -> bool:
    """A classified open item may be Review. An unresolved template or held decision may not validate."""
    if hasattr(record, 'metadata_readiness'):
        readiness = record.metadata_readiness
        template = record.template
        type_name = record.type
        note = _blob(record.review_note, record.notes, record.further_detail)
    else:
        readiness = record.get('Metadata Readiness') or ''
        template = record.get('Template') or ''
        type_name = record.get('Type') or ''
        note = _blob(record.get('Review Note'), record.get('Notes'), record.get('Further Detail'))
    if collapse(readiness) != 'Review':
        return False
    if 'held for review' in note or 'could reasonably fit' in note or 'unresolved classification' in note:
        return True
    return not collapse(template) or not collapse(type_name)


def prepare_inspection_output(output):
    """Canonicalize approved words and hold unsupported decisions for review.

    Filename groups are not changed. A collection template or collection type
    cannot remain Metadata Only. An action outside the master action table
    becomes Review and does not create a task.
    """
    for record in output.records:
        template = canonicalize(record.template, TEMPLATES | {'Metadata Only'})
        if template is not None and template != '':
            record.template = template
        example = canonicalize(record.type, EXAMPLE_TYPES | COLLECTION_TYPES)
        if example:
            record.type = example
        readiness = canonicalize(record.metadata_readiness, READINESS)
        if readiness:
            record.metadata_readiness = readiness
    apply_authorized_rules(output)
    for record in output.records:
        if _collection_conflict(record):
            shown = collapse(record.template) or 'Metadata Only'
            typed = collapse(record.type) or 'no type'
            record.record_kind = 'Normal Odyssey'
            record.metadata_readiness = 'Review'
            record.record_action_needed = 'Review'
            _note(
                record,
                f'Held for review: a {shown} / {typed} capture is a collection item, not a context-only Metadata Only reference.',
            )
            for asset in output.assets:
                if asset.assigned_record_id == record.proposed_record_id:
                    asset.action_needed = 'Review'
                    asset.queue_eligible = 'No'
                    asset.task_scope = ''

    for asset in output.assets:
        standard = canonicalize(asset.required_crop_standard, CROP_STANDARDS)
        if standard is not None and standard != '':
            asset.required_crop_standard = standard
        status = canonicalize(asset.qc_status, QC_STATUSES)
        action = canonicalize(asset.action_needed, ACTIONS)
        if action is None and collapse(asset.action_needed):
            original = collapse(asset.action_needed)
            asset.action_needed = 'Review'
            asset.queue_eligible = 'No'
            asset.task_scope = ''
            record = next((row for row in output.records if row.proposed_record_id == asset.assigned_record_id), None)
            if record is not None and record.record_kind != 'Metadata Only':
                record.metadata_readiness = 'Review'
                record.record_action_needed = 'Review'
                _note(record, f'Held for review: Action Needed {original!r} is not in the governing action table.')
        elif action is not None:
            asset.action_needed = action
        record = next((row for row in output.records if row.proposed_record_id == asset.assigned_record_id), None)
        derived = qc_status_for(asset.action_needed, record is not None and record.record_kind == 'Metadata Only')
        if derived:
            asset.qc_status = derived
        elif status is not None and status != '':
            asset.qc_status = status
        elif collapse(asset.qc_status) and record is not None:
            _note(record, f"QC Status {collapse(asset.qc_status)!r} is not Pass, Action Required, or Reference Only. The original status was kept.")

    apply_authorized_rules(output)
    for asset in output.assets:
        record = next((row for row in output.records if row.proposed_record_id == asset.assigned_record_id), None)
        derived = qc_status_for(asset.action_needed, record is not None and record.record_kind == 'Metadata Only')
        if derived:
            asset.qc_status = derived

    for record in output.records:
        if record.record_kind == 'Metadata Only' or record.metadata_readiness == 'Review':
            continue
        owned = [asset.action_needed for asset in output.assets if asset.assigned_record_id == record.proposed_record_id]
        if 'Additional Imaging Required' in owned and record.metadata_readiness in ('', 'Ready'):
            record.metadata_readiness = 'Blocked - Additional Imaging'
        elif any(action in QUEUE_ACTIONS for action in owned) and record.metadata_readiness in ('', 'Ready'):
            record.metadata_readiness = 'Blocked - Cropping/QC'
    return output
