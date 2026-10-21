"""Mechanical consequences of the governing packet.

These rules restate HistoryIT master, template, and cropping instructions.
They do not assign a filename to a benchmark row, and they do not choose
between types the packet leaves open.
"""
from __future__ import annotations

from app.vocabulary import prepare_inspection_output

DECISION_INSTRUCTIONS = """Governing decision order. Apply this before choosing a record ID. These are the client workflow rules already in the governing packet. They are not a filename answer key.

1. Physical boundaries come first. The front and verso of one sheet are one intellectual item. A blank verso stays on that item and is not Metadata Only. Distinct physical copies remain distinct records even when the words or pictures match. A batch boundary or a folder boundary does not close an item by itself. When context.prior_open_item is set, continue that record if the next authorized image is the next page or verso of the same physical item. Page sequence and continuation evidence decide whether it continues. A repeated image that is a separate sheet, with its own verso or different edge wear, is a new record.

2. Metadata Only is only for a file folder or organizational enclosure, a standalone project-identifier sheet, or a descriptive inventory or caption list that describes the surrounding items as context rather than being a collection item. That reference and its blank verso are one Metadata Only reference, with no production task. A blank verso of a letter, photograph, publication, or other collection item stays on that Normal Odyssey record and is not Metadata Only. A descriptive sheet that supports one specific item stays with that item. A mailed envelope is correspondence, not a file-folder enclosure, and its back stays with that envelope. A letter, a scrapbook page, a clipping, a photograph, a publication, or research notes that are themselves a collection item is a Normal Odyssey record. Do not use Metadata Only because a page mentions the chapter, box, or project, and do not use it for every caption or every blank verso. Default a true Metadata Only asset to QC Status Reference Only, Action Needed N/A, and no production task.

3. Template must be one of the 18 Odyssey templates, or Metadata Only for a true reference capture. A bound notebook of biographical writing is Document / Biographical Notes; record the notebook binding as physical-form detail. Use Ledger only for an account or transaction record. Research notes or a citation index that are themselves a collection item, together with that item's verso, are Document / Research Notes. A descriptive inventory of the surrounding items is not Research Notes. A mailed envelope and its back are Correspondence / Envelope unless the letter itself is visible. A blank file folder is Metadata Only. A publication that shows a volume and number, or continues onto another page of the same issue, is Magazine. Use Newspaper only when a newspaper masthead is visible. Use the page, not the filename. Type is subordinate to Template. If the page still fits two templates after these rules, mark Metadata Readiness Review and name both.

4. QC Status is Pass when no production action is needed, Action Required when a real production action is pending, and Reference Only for a metadata reference. Action Needed is No Adjustment only when the supplied image already satisfies the applicable standard. Required Crop Standard does not by itself create a task. Overhead-camera archive material on a black capture background uses an archival border: a slight visible edge around the item, not a large black field. Substantial or excess black capture background means the image fails that standard. Use Archival Border Crop for a non-publication archival item or photograph, Publication Crop for a publication page, and Crop / Recrop with Book Page Edge for a book page. Set QC Status Action Required and Metadata Readiness Blocked - Cropping/QC. A thin capture margin or a slight archival edge around an item that already fills the frame is compliant and is not a crop. Black that is part of the item, including a print's own border, ink, or picture, is not capture background. Do not crop away content or a compliant archival border. Judge orientation from the content, not the filename. A genuine 180-degree or 90-degree mis-orientation is Rotate, Action Required, and Blocked - Cropping/QC. Do not rotate an upright photograph, a blank image, or an intentional layout. If orientation cannot be determined, use Review rather than choosing a rotation. Otherwise use one governing action name exactly: Crop / Recrop, Publication Crop, Archival Border Crop, Deskew, Rotate, Split Spread, Split Spread + Publication Crop, Create Derivative Crop, Crop / Recrop + Create Derivative Crop, Reshoot, Additional Imaging Required, or Review. A bound book or notebook, including its cover and blank versos, is one item. Crop a book page when a large background remains around the page; the standard is Book Page Edge and the action is Crop / Recrop, not Publication Crop. A capture that is the photographic print itself uses Archival Border and No Adjustment when only the slight archival edge remains. When a mounted photograph on a notebook page needs its own cropped deliverable, add a distinct Create Derivative Crop. The front, written verso, and numbered back of one photographic print are one Photographic Image. A nearly blank back that carries only an identification number is that verso, not Additional Imaging Required. Each scrapbook or album page is its own record. An open two-page capture is a spread and uses Split Spread; do not combine the cover, the spread, and the back into one record. A yearbook page, an attached note on that page, and its show-through verso are one item; the verso is not Additional Imaging Required. A publication page uses Publication Page Edge. A second physical copy of the same article, distinguished by a different crease, edge, or show-through, is a separate record, and its verso stays with that copy. Required Crop Standard is Archival Border, Page Edge, Publication Page Edge, Book Page Edge, Tight Border, or N/A. Production Queue Eligible is Yes only for a real crop, deskew, split, rotate, reshoot, derivative, or additional-imaging action. Logical Component Count counts logical surfaces. A blank verso is its own surface. An attached-note capture and the clean capture of that same surface may count as one surface.

5. Approved calibration examples apply only to the folders named in that example. Do not copy an example onto later filenames when the images disagree.
"""

_NO_QUEUE = frozenset({'', 'no adjustment', 'n/a', 'review'})
_QUEUE_MARKERS = ('crop', 'recrop', 'deskew', 'split spread', 'derivative', 'additional imaging', 'rotate', 'reshoot')


def decision_instructions() -> str:
    return DECISION_INSTRUCTIONS


def queues_for_action(action: str) -> bool | None:
    """Return whether the governing action list creates a production task.

    None means the action is outside the governing list and must be left unchanged.
    """
    text = ' '.join(str(action or '').casefold().split())
    if text in _NO_QUEUE:
        return False
    if any(marker in text for marker in _QUEUE_MARKERS):
        return True
    return None


def apply_governing_constraints(output):
    """Align queue eligibility and Metadata Only defaults. Do not move assets between records."""
    prepare_inspection_output(output)
    records_by_id = {record.proposed_record_id: record for record in output.records}
    for asset in output.assets:
        record = records_by_id.get(asset.assigned_record_id)
        if record is not None and record.record_kind == 'Metadata Only':
            asset.qc_status = 'Reference Only'
            asset.action_needed = 'N/A'
            asset.queue_eligible = 'No'
            asset.task_scope = ''
            continue
        decision = queues_for_action(asset.action_needed)
        if decision is True:
            asset.queue_eligible = 'Yes'
        elif decision is False:
            asset.queue_eligible = 'No'
    for record in output.records:
        owned = [asset for asset in output.assets if asset.assigned_record_id == record.proposed_record_id]
        if record.record_kind == 'Metadata Only':
            record.metadata_readiness = 'Metadata Reference'
            record.record_action_needed = 'N/A'
            record.production_task_count = '0'
            continue
        record.production_task_count = str(sum(1 for asset in owned if asset.queue_eligible == 'Yes'))
    return output
