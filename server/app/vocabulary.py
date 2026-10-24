"""Approved controlled vocabulary from the governing packet.

Capitalization and whitespace may be canonicalized to the approved spelling.
A different decision is not renamed. In particular, Needs Adjustment is not
treated as Action Required: the master action table does not give those words
the same meaning.
"""
from __future__ import annotations

import re

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
    'Rotate + Archival Border Crop',
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
    'Rotate + Archival Border Crop',
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


_CLAUSE_BREAK = re.compile(r'[.!?;:]')
_NEGATION_SUFFIX = re.compile(
    r'(?:^|\s)(?:no|not|without|absent|lacks?|none)\b(?:\s+[a-z]+){0,4}\s*$'
)
_TRAILING_DENIAL = re.compile(
    r'^(?:\s+[a-z]+){0,4}\s+(?:is|are|was|were)\s+(?:absent|missing|not\s+present)\b'
)


def _affirmed(text: str, phrase: str) -> bool:
    """True when one occurrence is a whole word and that clause does not deny it.

    A denial before or after the phrase negates that occurrence only.
    Another clause can still affirm the same phrase.
    """
    haystack = collapse(text).casefold()
    needle = collapse(phrase).casefold()
    if not needle:
        return False
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        end = index + len(needle)
        bounded = (index == 0 or not haystack[index - 1].isalnum()) and (end == len(haystack) or not haystack[end].isalnum())
        if bounded and not _negated(haystack, index, end):
            return True
        start = end


def _negated(text: str, index: int, end: int) -> bool:
    before = _CLAUSE_BREAK.split(text[max(0, index - 48):index])[-1]
    if re.search(r'(?:^|\s)non-$', before) or _NEGATION_SUFFIX.search(before):
        return True
    after = _CLAUSE_BREAK.split(text[end:end + 64], maxsplit=1)[0]
    return _TRAILING_DENIAL.search(after) is not None


def _context_inventory(blob: str) -> bool:
    """A sheet that describes surrounding items as context, not one item's research notes."""
    text = collapse(blob).casefold()
    if any(_affirmed(text, phrase) for phrase in (
        'associated with this one', 'supports this one', 'attached to this item', 'stays with this item',
    )):
        return False
    return any(_affirmed(text, phrase) for phrase in (
        'descriptive inventory', 'describes the surrounding', 'describe the surrounding',
        'functions as context', 'function as context', 'context-only capture',
    ))


def _blank_without_orientation(note: str) -> bool:
    text = collapse(note).casefold()
    if not _affirmed(text, 'blank'):
        return False
    return not any(_affirmed(text, token) for token in ('180', 'upside down', 'upside', 'inverted', 'sideways'))


def _orientation_uncertain(note: str) -> bool:
    text = collapse(note).casefold()
    return _affirmed(text, 'cannot be determined') or _affirmed(text, 'orientation is uncertain') or _affirmed(text, 'uncertain orientation')


def _fails_archival_border(note: str) -> bool:
    text = collapse(note).casefold()
    return any(_affirmed(text, phrase) for phrase in (
        'excessive', 'excess', 'large background', 'substantial', 'most of the frame',
        'capture background', 'black field',
    ))


def _margin_only(note: str) -> bool:
    """A slight edge is not a crop. Excess capture background is a real defect."""
    text = collapse(note).casefold()
    if not text:
        return False
    if any(_affirmed(text, phrase) for phrase in (
        'white physical border', 'integral physical border', 'attached note', 'attached-note', 'own dark edge',
    )):
        return False
    if _fails_archival_border(text) or any(word in text for word in (
        'cut off', 'clipped', 'split', 'spread', 'two pages', 'two-page', 'skewed', 'crooked',
    )):
        return False
    if any(_affirmed(text, phrase) for phrase in (
        'slight archival', 'compliant slight', 'a little over 75', 'somewhat above 75', 'little over 75',
    )):
        return True
    return any(word in text for word in ('border', 'margin', 'surround', 'background'))


def _clearly_excessive_background(note: str) -> bool:
    """Only an affirmed excess is a crop. A black border by itself is not."""
    return any(_affirmed(note, phrase) for phrase in (
        'clearly excessive', 'genuinely excessive', 'excessive capture background',
        'excess capture background', 'excessive black background', 'excessive black capture',
    ))


def _integral_binding(note: str) -> bool:
    return any(_affirmed(note, phrase) for phrase in (
        'binding cord', 'staples', 'staple', 'rings', 'sewing', 'sewn binding',
        'bound program', 'bound publication', 'integral binding',
    ))


def _unbound_publication(note: str) -> bool:
    if _integral_binding(note):
        return False
    return any(_affirmed(note, phrase) for phrase in (
        'unbound publication', 'unbound newsletter', 'unbound article',
        'loose publication sheet', 'extant page edge',
    ))


_SAME_REPORT = (
    'same chapter', 'same term', 'internal section heading', 'section heading',
    'same report packet', 'consecutive report section', 'same chapter/term',
)
_TRUE_BOUNDARY = (
    'true document boundary', 'physical document boundary', 'separate document',
    'different report', 'new physical document', 'different physical document',
)


def _report_continuation(record, assets) -> bool:
    blob = _record_blob(record, assets)
    if any(_affirmed(blob, phrase) for phrase in _TRUE_BOUNDARY):
        return False
    if record.record_kind == 'Metadata Only':
        return False
    if record.type not in {'Report', ''} and 'report' not in blob:
        return False
    return any(_affirmed(blob, phrase) for phrase in _SAME_REPORT)


def keep_report_packets(output):
    """Keep consecutive sections of one chapter/term report on one record.

    An internal section heading is not a boundary. A true physical document
    boundary stays a separate record. This does not match filenames.
    """
    by_record = {}
    for asset in output.assets:
        by_record.setdefault(asset.assigned_record_id, []).append(asset)
    kept = []
    for record in output.records:
        owned = by_record.get(record.proposed_record_id, [])
        previous = kept[-1] if kept else None
        if previous is not None and _report_continuation(record, owned):
            previous_assets = by_record.get(previous.proposed_record_id, [])
            if previous.record_kind != 'Metadata Only' and (previous.type in {'Report', ''} or 'report' in _record_blob(previous, previous_assets)):
                for asset in owned:
                    asset.assigned_record_id = previous.proposed_record_id
                by_record.setdefault(previous.proposed_record_id, []).extend(owned)
                continue
        kept.append(record)
    output.records = kept
    return output


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
        if research and not _context_inventory(blob) and not any(word in blob for word in ('file folder', 'manila')):
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
        if _context_inventory(blob) and 'scrapbook' not in blob:
            record.record_kind = 'Metadata Only'
            record.template = 'Metadata Only'
            record.type = ''
        bound_book = notebook or (record.template == 'Document' and record.type == 'Biographical Notes') or record.template == 'Ledger'
        record_note = _blob(record.review_note, record.further_detail)
        mounted_on_final = 'mounted photograph' in record_note and 'final' in record_note
        for index, asset in enumerate(owned):
            asset_blob = _blob(asset.asset_role, asset.qc_note, asset.task_note, asset.exception_note)
            mounted = any(phrase in asset_blob for phrase in (
                'mounted photograph', 'mounted photo', 'photograph mounted', 'tipped-in', 'pasted photograph', 'photo pasted',
            )) or (mounted_on_final and index == len(owned) - 1)
            binding = _integral_binding(asset_blob)
            unbound = _unbound_publication(asset_blob)
            excessive = _clearly_excessive_background(asset_blob)
            if binding and asset.action_needed == 'Publication Crop':
                if excessive:
                    asset.action_needed = 'Review'
                    asset.required_crop_standard = 'N/A'
                    _note(record, 'Held for review: a page-edge crop would sever integral binding.')
                else:
                    asset.action_needed = 'No Adjustment'
                    asset.required_crop_standard = 'N/A'
                    _note(record, 'Bound publication binding is preserved.')
            elif unbound and asset.action_needed in {'No Adjustment', 'Publication Crop'}:
                asset.action_needed = 'Publication Crop'
                asset.required_crop_standard = 'Publication Page Edge'
            elif asset.action_needed == 'No Adjustment' and excessive and not binding:
                asset.action_needed = 'Archival Border Crop'
                asset.required_crop_standard = 'Archival Border'
            elif mounted and asset.action_needed in {'Crop / Recrop', 'Publication Crop', 'Crop', 'No Adjustment', 'Archival Border Crop', ''}:
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
                if _fails_archival_border(asset_blob):
                    asset.required_crop_standard = 'Archival Border'
                else:
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
            if asset.action_needed == 'Rotate' and _orientation_uncertain(asset_blob):
                asset.action_needed = 'Review'
            elif asset.action_needed == 'Rotate' and _blank_without_orientation(asset_blob):
                asset.action_needed = 'No Adjustment'
        if any(asset.action_needed == 'Review' and _orientation_uncertain(_blob(asset.qc_note, asset.task_note)) for asset in owned):
            queued = any(asset.action_needed in QUEUE_ACTIONS for asset in owned)
            if not queued and record.metadata_readiness in {'', 'Ready', 'Blocked - Cropping/QC'}:
                record.metadata_readiness = 'Review'
                record.record_action_needed = 'Review'
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

    keep_report_packets runs first so a section heading is not a second record.

    Filename groups are not changed. A collection template or collection type
    cannot remain Metadata Only. An action outside the master action table
    becomes Review and does not create a task.
    """
    keep_report_packets(output)
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
