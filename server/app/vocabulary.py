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
    if _fails_archival_border(text) or any(word in text for word in (
        'cut off', 'clipped', 'split', 'spread', 'two pages', 'two-page', 'skewed', 'crooked',
    )):
        return False
    return any(word in text for word in ('border', 'margin', 'surround', 'background'))


_EXCESS_ASSESSMENT = frozenset({'excess capture background', 'excess black capture background'})
_SLIGHT_ASSESSMENT = frozenset({'compliant slight edge', 'slight archival edge'})
_NONE_ASSESSMENT = frozenset({'item black', 'not applicable'})
_CROP_ACTIONS = frozenset({'Archival Border Crop', 'Crop / Recrop', 'Publication Crop', 'Crop'})
_EXCESS_PHRASES = (
    'excessive', 'excess', 'large background', 'substantial', 'most of the frame',
    'capture background', 'black field',
)
_UNRESOLVED_CROP = (
    'Excess capture background is affirmed, and Action Needed does not queue an archival crop. '
    'No crop task was created from that contradiction. Human review is required before this record can be Ready.'
)
_UNRESOLVED_SECOND = (
    'Rotate is retained. Excess capture background is also affirmed. '
    'One Action Needed cannot queue both Rotate and an archival crop, so the crop remains unresolved for human review.'
)
_UNRESOLVED_TURN = (
    'The crop action is retained. A rotation finding is also affirmed in the image note. '
    'One Action Needed cannot queue both, so the rotation remains unresolved for human review.'
)
_CONTRADICTION = (
    'The border assessment and the image note disagree. '
    'No crop task was created from that contradiction. Human review is required before this record can be Ready.'
)


def _border_assessment(asset) -> str:
    return collapse(getattr(asset, 'border_assessment', '')).casefold()


def _note_denies_excess(note: str) -> bool:
    """True when an excess phrase appears only as a negation."""
    text = collapse(note).casefold()
    if any(_affirmed(text, phrase) for phrase in _EXCESS_PHRASES):
        return False
    return any(phrase in text for phrase in _EXCESS_PHRASES)


def _slight_note(note: str) -> bool:
    text = collapse(note).casefold()
    if _fails_archival_border(text):
        return False
    return any(_affirmed(text, phrase) for phrase in (
        'slight archival edge', 'slight edge', 'compliant slight edge',
        'compliant archival border', 'thin archival border',
    ))


def _structured_border(asset) -> str:
    """Map Border Assessment to excess, slight, none, unknown, or empty.

    Empty means an older response did not supply the field. Unknown means the
    value is present but not one of the allowed findings.
    """
    assessment = _border_assessment(asset)
    if assessment in _EXCESS_ASSESSMENT:
        return 'excess'
    if assessment in _SLIGHT_ASSESSMENT:
        return 'slight'
    if assessment in _NONE_ASSESSMENT:
        return 'none'
    if assessment:
        return 'unknown'
    return ''


def border_resolution(asset) -> str:
    """Return excess, slight, none, unresolved, or empty.

    The image finding is Border Assessment and the QC note. A production-task
    note does not decide the border. An empty result means those findings do
    not call for a crop or a hold.
    """
    structured = _structured_border(asset)
    note = collapse(asset.qc_note)
    note_excess = _fails_archival_border(note)
    if structured == 'unknown':
        return 'unresolved'
    if structured == 'excess' and _note_denies_excess(note):
        return 'unresolved'
    if structured in {'slight', 'none'} and note_excess:
        return 'unresolved'
    if structured == 'excess' or (not structured and note_excess):
        return 'excess'
    if structured == 'slight' or (not structured and _slight_note(note)):
        return 'slight'
    if structured == 'none':
        return 'none'
    return ''


def _excess_affirmed(asset) -> bool:
    return _structured_border(asset) == 'excess' or _fails_archival_border(asset.qc_note)


def crop_findings_conflict(asset) -> bool:
    """True when Action Needed, Border Assessment, and the QC note disagree.

    A production-task note is not a finding. Mentioning the slight-edge
    standard beside an affirmed excess, as in "beyond the slight archival
    edge", is not a conflict. Ambiguous margin wording does not cancel an
    explicit crop by itself.
    """
    kind = _structured_border(asset)
    note = asset.qc_note or ''
    action = asset.action_needed
    if kind == 'unknown':
        return True
    positive = action in _CROP_ACTIONS or kind == 'excess' or _fails_archival_border(note)
    negative = kind in {'slight', 'none'} or _note_denies_excess(note) or (
        _slight_note(note) and not _fails_archival_border(note)
    )
    if action in {'No Adjustment', ''}:
        negative = True
    if action in _CROP_ACTIONS and kind != 'excess' and not _fails_archival_border(note) and _margin_only(note):
        return True
    return bool(positive and negative)


def _affirmed_turn(note: str) -> bool:
    text = collapse(note).casefold()
    if _orientation_uncertain(text):
        return False
    return any(_affirmed(text, phrase) for phrase in ('180-degree', '180 degree', 'upside down'))


def _hold_border_review(record, sentence: str) -> None:
    if record.record_kind == 'Metadata Only':
        return
    if record.metadata_readiness in {'', 'Ready', 'Blocked - Cropping/QC'}:
        record.metadata_readiness = 'Review'
        record.record_action_needed = 'Review'
    _note(record, sentence)


def _clear_pass(asset) -> None:
    asset.action_needed = 'Review'
    asset.qc_status = ''
    asset.queue_eligible = 'No'
    asset.task_scope = ''


def _preserve_border_reason(asset) -> None:
    """Copy Border Assessment into the QC note so the register keeps the reason."""
    assessment = collapse(getattr(asset, 'border_assessment', ''))
    if not assessment:
        return
    sentence = f'Border Assessment: {assessment}.'
    current = collapse(asset.qc_note)
    if sentence.casefold() in current.casefold():
        return
    asset.qc_note = f'{current} {sentence}'.strip()


def _border_review_locked(record) -> bool:
    note = collapse(record.review_note).casefold()
    return (
        'human review is required before this record can be ready' in note
        or 'crop remains unresolved for human review' in note
    )


def _conflict_sentence(action: str, assessment: str) -> str:
    shown = assessment or 'missing'
    return (
        f'Crop findings conflict. Action Needed was {action or "blank"}. '
        f'Border Assessment was {shown}. The original QC Note was kept. '
        'No crop task was created from that contradiction. '
        'Human review is required before this record can be Ready.'
    )


def _apply_border_hold(record, asset, resolution: str) -> None:
    """Stop a false Pass. Do not invent a crop, and do not replace Rotate."""
    if record.record_kind == 'Metadata Only':
        return
    conflict = crop_findings_conflict(asset)
    assessment = collapse(getattr(asset, 'border_assessment', ''))
    if asset.action_needed == 'Rotate' and (_excess_affirmed(asset) or conflict):
        _preserve_border_reason(asset)
        if _excess_affirmed(asset):
            _hold_border_review(record, f'{_UNRESOLVED_SECOND} Border Assessment was {assessment or "missing"}.')
        else:
            _hold_border_review(record, _conflict_sentence('Rotate', assessment))
        return
    if conflict or resolution == 'unresolved':
        action = collapse(asset.action_needed)
        _preserve_border_reason(asset)
        _clear_pass(asset)
        if _excess_affirmed(asset) and action in {'No Adjustment', ''}:
            _hold_border_review(record, f'{_UNRESOLVED_CROP} Border Assessment was {assessment or "missing"}.')
        else:
            _hold_border_review(record, _conflict_sentence(action, assessment))
        return
    if resolution == 'excess' and asset.action_needed in _CROP_ACTIONS and _affirmed_turn(asset.qc_note):
        _preserve_border_reason(asset)
        _hold_border_review(record, f'{_UNRESOLVED_TURN} Border Assessment was {assessment or "missing"}.')
        return
    if resolution == 'excess' and asset.action_needed in _CROP_ACTIONS:
        _preserve_border_reason(asset)


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
            resolution = border_resolution(asset)
            conflict = crop_findings_conflict(asset)
            mounted = any(phrase in asset_blob for phrase in (
                'mounted photograph', 'mounted photo', 'photograph mounted', 'tipped-in', 'pasted photograph', 'photo pasted',
            )) or (mounted_on_final and index == len(owned) - 1)
            preserved_mounted = False
            if mounted and asset.action_needed in {'Crop / Recrop', 'Publication Crop', 'Crop', 'No Adjustment', 'Archival Border Crop', ''}:
                preserved_mounted = True
                asset.action_needed = 'Crop / Recrop + Create Derivative Crop'
                if asset.required_crop_standard in {'', 'Page Edge', 'Publication Page Edge', 'Archival Border'}:
                    asset.required_crop_standard = 'Book Page Edge'
                if collapse(asset.expected_output_asset_count) in {'', '0', '1'}:
                    asset.expected_output_asset_count = '2'
            elif not conflict and resolution != 'unresolved' and record.template in {'Document', 'Correspondence'} and not bound_book and asset.action_needed == 'Archival Border Crop':
                if _fails_archival_border(asset.qc_note) or resolution == 'excess':
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
            if not preserved_mounted:
                _apply_border_hold(record, asset, resolution)
        if record.record_kind != 'Metadata Only' and _border_review_locked(record):
            record.metadata_readiness = 'Review'
            record.record_action_needed = 'Review'
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
