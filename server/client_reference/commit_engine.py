"""HistoryIT deterministic staging-to-production commit planner.

This module contains NO image or source-folder access.  It converts completed
Lean Split v2 staging rows into deterministic production rows and validates the
batch contract.  A storage adapter can apply the returned CommitPlan to Google
Sheets, a database, or a test fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
import re


READY_VALUES = {
    "Ready",
    "Blocked - Cropping/QC",
    "Blocked - Additional Imaging",
    "Review",
}
RECORD_KINDS = {"Normal Odyssey", "Metadata Only"}
RECORD_OPERATIONS = {"Create New", "Update Existing Open Record"}


class CommitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommitMetrics:
    source_assets: int
    odyssey_records: int
    metadata_references: int
    ready_records: int
    blocked_crop_qc: int
    blocked_additional_imaging: int
    review_records: int
    content_linked_assets: int
    logical_components: int
    production_tasks: int
    crop_qc_tasks: int
    additional_imaging_tasks: int


@dataclass
class CommitPlan:
    batch_id: str
    processing_unit: str
    append_rows: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    update_rows_by_key: Dict[str, List[Tuple[str, str, Dict[str, str]]]] = field(default_factory=dict)
    resulting_open_item: str = "None"
    new_task_count: int = 0
    new_normal_record_count: int = 0
    new_reference_count: int = 0
    newly_ready_count: int = 0
    blocked_record_count: int = 0
    validation_notes: List[str] = field(default_factory=list)
    idempotent_noop: bool = False


# ---------- generic helpers ----------

def normalize(v: object) -> str:
    return "" if v is None else str(v).strip()


def parse_int(v: object, field_name: str) -> int:
    s = normalize(v)
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError as exc:
        raise CommitError(f"Invalid integer for {field_name}: {v!r}") from exc


def split_assets(value: object) -> List[str]:
    return [x.strip() for x in normalize(value).split(";") if x.strip()]


def unique_index(rows: Sequence[Mapping[str, str]], key: str) -> Dict[str, Mapping[str, str]]:
    out: Dict[str, Mapping[str, str]] = {}
    for row in rows:
        k = normalize(row.get(key))
        if not k:
            continue
        if k in out:
            raise CommitError(f"Duplicate {key}: {k}")
        out[k] = row
    return out


def count_if(rows: Sequence[Mapping[str, str]], key: str, value: str) -> int:
    return sum(1 for r in rows if normalize(r.get(key)) == value)


# ---------- preflight ----------

def validate_staging(
    queue_row: Mapping[str, str],
    asset_rows: Sequence[Mapping[str, str]],
    record_rows: Sequence[Mapping[str, str]],
    existing_asset_qc_rows: Sequence[Mapping[str, str]],
) -> None:
    status = normalize(queue_row.get("Batch Status"))
    if status == "Committed - Validated":
        return
    if status != "Inspection Complete - Awaiting Commit":
        raise CommitError(f"Batch not commit-ready: {status!r}")

    planned = parse_int(queue_row.get("Planned Asset Count"), "Planned Asset Count")
    if planned != len(asset_rows):
        raise CommitError(f"Planned asset count {planned} != staged asset count {len(asset_rows)}")

    filenames = [normalize(r.get("Digital Asset")) for r in asset_rows]
    if any(not f for f in filenames):
        raise CommitError("Every Asset Staging row must have Digital Asset")
    if len(set(filenames)) != len(filenames):
        raise CommitError("Duplicate Digital Asset inside Asset Staging")

    existing_names = {normalize(r.get("Digital Asset")) for r in existing_asset_qc_rows}
    dupes = sorted(set(filenames) & existing_names)
    if dupes:
        raise CommitError(f"Staged assets already exist in Asset QC: {dupes}")

    for r in asset_rows:
        if normalize(r.get("Inspection Complete")) != "Yes":
            raise CommitError(f"Asset not inspection-complete: {r.get('Digital Asset')}")
        if normalize(r.get("Commit Status")) != "Pending Commit":
            raise CommitError(f"Asset not Pending Commit: {r.get('Digital Asset')}")

    rec_by_id = unique_index(record_rows, "Proposed Record ID")
    for r in record_rows:
        rid = normalize(r.get("Proposed Record ID"))
        if normalize(r.get("Inspection Complete")) != "Yes":
            raise CommitError(f"Record not inspection-complete: {rid}")
        if normalize(r.get("Commit Status")) != "Pending Commit":
            raise CommitError(f"Record not Pending Commit: {rid}")
        kind = normalize(r.get("Record Kind"))
        op = normalize(r.get("Record Operation"))
        readiness = normalize(r.get("Metadata Readiness"))
        if kind not in RECORD_KINDS:
            raise CommitError(f"Unsupported Record Kind for {rid}: {kind!r}")
        if op not in RECORD_OPERATIONS:
            raise CommitError(f"Unsupported Record Operation for {rid}: {op!r}")
        if kind == "Normal Odyssey" and readiness not in READY_VALUES:
            raise CommitError(f"Unsupported Metadata Readiness for {rid}: {readiness!r}")

    for a in asset_rows:
        rid = normalize(a.get("Assigned Record ID"))
        if rid not in rec_by_id:
            raise CommitError(f"Asset {a.get('Digital Asset')} refers to unstaged record {rid}")


# ---------- deterministic mappings ----------

def build_asset_qc_row(asset: Mapping[str, str], record: Mapping[str, str]) -> Dict[str, str]:
    kind = normalize(record.get("Record Kind"))
    template = normalize(record.get("Template")) if kind == "Normal Odyssey" else "Metadata Only"
    type_value = normalize(record.get("Type")) if kind == "Normal Odyssey" else ""
    return {
        "Project": normalize(asset.get("Project")),
        "Processing Unit / Source Folder": normalize(asset.get("Processing Unit / Source Folder")),
        "Digital Asset": normalize(asset.get("Digital Asset")),
        "Box": normalize(asset.get("Box")),
        "Source Group / Folder": normalize(asset.get("Source Group / Folder")),
        "Assigned Record ID": normalize(asset.get("Assigned Record ID")),
        "Template": template,
        "Type": type_value,
        "Asset Role": normalize(asset.get("Asset Role")),
        "Required Crop Standard": normalize(asset.get("Required Crop Standard")),
        "QC Status": normalize(asset.get("QC Status")),
        "Action Needed": normalize(asset.get("Action Needed")),
        "Expected Output Asset Count": normalize(asset.get("Expected Output Asset Count")),
        "QC Confidence": normalize(asset.get("QC Confidence")),
        "QC Note": normalize(asset.get("QC Note")),
        "Odyssey Ingest / Visibility Note": normalize(asset.get("Odyssey Ingest / Visibility Note")),
        "Production Queue Eligible": normalize(asset.get("Production Queue Eligible")),
        "Exception / Duplicate Note": normalize(asset.get("Exception / Duplicate Note")),
    }


def build_itemization_row(record: Mapping[str, str]) -> Dict[str, str]:
    return {
        "Project": normalize(record.get("Project")),
        "Processing Unit / Source Folder": normalize(record.get("Processing Unit / Source Folder")),
        "Item ID": normalize(record.get("Proposed Record ID")),
        "Box": normalize(record.get("Box")),
        "Source Group / Folder": normalize(record.get("Source Group / Folder")),
        "Parent Scrapbook": normalize(record.get("Parent Scrapbook")),
        "Parent Scrapbook Page": normalize(record.get("Parent Scrapbook Page")),
        "Digital Assets": normalize(record.get("Full Record Assets After Commit")),
        "Digital Asset Count": normalize(record.get("Digital Asset Count")),
        "Logical Component Count": normalize(record.get("Logical Component Count")),
        "Template": normalize(record.get("Template")),
        "Type": normalize(record.get("Type")),
        "Further Detail": normalize(record.get("Further Detail")),
        "Required Crop Standard": normalize(record.get("Required Crop Standard")),
        "Metadata Readiness": normalize(record.get("Metadata Readiness")),
        "Confidence": normalize(record.get("Confidence")),
        "Review Note": normalize(record.get("Review Note")),
        "Production Task Count": normalize(record.get("Production Task Count")),
        "Notes": normalize(record.get("Notes")),
    }


def build_metadata_reference_row(record: Mapping[str, str]) -> Dict[str, str]:
    return {
        "Project": normalize(record.get("Project")),
        "Processing Unit / Source Folder": normalize(record.get("Processing Unit / Source Folder")),
        "Reference ID": normalize(record.get("Proposed Record ID")),
        "Box": normalize(record.get("Box")),
        "Source Group / Folder": normalize(record.get("Source Group / Folder")),
        "Digital Assets": normalize(record.get("Full Record Assets After Commit")),
        "Digital Asset Count": normalize(record.get("Digital Asset Count")),
        "Reference Role": normalize(record.get("Reference Role")),
        "Template": "Metadata Only",
        "QC Status": normalize(record.get("Record QC Status")),
        "Action Needed": normalize(record.get("Record Action Needed")),
        "Metadata Readiness": normalize(record.get("Metadata Readiness")),
        "Odyssey Ingest / Visibility Note": normalize(record.get("Odyssey Ingest / Visibility Note")),
        "Notes": normalize(record.get("Notes")) or normalize(record.get("Further Detail")),
    }


def task_prefix_from_record_id(record_id: str) -> str:
    # TDC-B19-F05-001 -> TDC-B19-F05-TASK-
    m = re.match(r"^(.*-F\d+)-", record_id)
    if not m:
        raise CommitError(f"Cannot derive task prefix from record id {record_id}")
    return f"{m.group(1)}-TASK-"


def next_task_sequence(existing_tasks: Sequence[Mapping[str, str]], prefix: str) -> int:
    max_n = 0
    pat = re.compile(re.escape(prefix) + r"(\d+)$")
    for r in existing_tasks:
        tid = normalize(r.get("Task ID"))
        m = pat.match(tid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def build_task_rows(
    assets: Sequence[Mapping[str, str]],
    records_by_id: Mapping[str, Mapping[str, str]],
    existing_tasks: Sequence[Mapping[str, str]],
) -> List[Dict[str, str]]:
    counters: Dict[str, int] = {}
    out: List[Dict[str, str]] = []
    for asset in assets:
        if normalize(asset.get("Production Queue Eligible")) != "Yes":
            continue
        rid = normalize(asset.get("Assigned Record ID"))
        rec = records_by_id[rid]
        prefix = task_prefix_from_record_id(rid)
        if prefix not in counters:
            counters[prefix] = next_task_sequence(existing_tasks, prefix)
        seq = counters[prefix]
        counters[prefix] += 1
        out.append({
            "Project": normalize(asset.get("Project")),
            "Processing Unit / Source Folder": normalize(asset.get("Processing Unit / Source Folder")),
            "Task ID": f"{prefix}{seq:03d}",
            "Assigned Record ID": rid,
            "Digital Asset": normalize(asset.get("Digital Asset")),
            "Box": normalize(asset.get("Box")),
            "Source Group / Folder": normalize(asset.get("Source Group / Folder")),
            "Template": normalize(rec.get("Template")),
            "Type": normalize(rec.get("Type")),
            "Action Needed": normalize(asset.get("Action Needed")),
            "Task Scope": normalize(asset.get("Production Task Scope")),
            "Expected Output Asset Count": normalize(asset.get("Expected Output Asset Count")),
            "Task Status": "Open",
            "QC Confidence": normalize(asset.get("QC Confidence")),
            "Production Note": normalize(asset.get("Production Task Note")),
            "Carried Forward From": "",
        })
    return out


def build_ready_row(record: Mapping[str, str], ready_date: str) -> Dict[str, str]:
    return {
        "Project": normalize(record.get("Project")),
        "Processing Unit / Source Folder": normalize(record.get("Processing Unit / Source Folder")),
        "Item ID": normalize(record.get("Proposed Record ID")),
        "Box": normalize(record.get("Box")),
        "Source Group / Folder": normalize(record.get("Source Group / Folder")),
        "Parent Scrapbook": normalize(record.get("Parent Scrapbook")),
        "Parent Scrapbook Page": normalize(record.get("Parent Scrapbook Page")),
        "Digital Assets": normalize(record.get("Full Record Assets After Commit")),
        "Digital Asset Count": normalize(record.get("Digital Asset Count")),
        "Template": normalize(record.get("Template")),
        "Type": normalize(record.get("Type")),
        "Metadata Readiness": "Ready",
        "Ready Date": ready_date,
        "Notes": normalize(record.get("Notes")) or normalize(record.get("Review Note")),
    }


# ---------- plan construction ----------

def plan_commit(
    queue_row: Mapping[str, str],
    asset_rows: Sequence[Mapping[str, str]],
    record_rows: Sequence[Mapping[str, str]],
    existing: Mapping[str, Sequence[Mapping[str, str]]],
    ready_date: str,
) -> CommitPlan:
    batch_id = normalize(queue_row.get("Batch ID"))
    processing_unit = normalize(asset_rows[0].get("Processing Unit / Source Folder")) if asset_rows else ""

    if normalize(queue_row.get("Batch Status")) == "Committed - Validated":
        return CommitPlan(batch_id=batch_id, processing_unit=processing_unit, idempotent_noop=True)

    validate_staging(queue_row, asset_rows, record_rows, existing.get("Asset QC", []))

    rec_by_id = unique_index(record_rows, "Proposed Record ID")
    plan = CommitPlan(batch_id=batch_id, processing_unit=processing_unit)

    plan.append_rows["Asset QC"] = [build_asset_qc_row(a, rec_by_id[normalize(a.get("Assigned Record ID"))]) for a in asset_rows]

    existing_items = unique_index(existing.get("Itemization", []), "Item ID")
    existing_refs = unique_index(existing.get("Metadata References", []), "Reference ID")
    existing_ready = unique_index(existing.get("Ready for Metadata", []), "Item ID")

    for rec in record_rows:
        rid = normalize(rec.get("Proposed Record ID"))
        kind = normalize(rec.get("Record Kind"))
        op = normalize(rec.get("Record Operation"))
        readiness = normalize(rec.get("Metadata Readiness"))

        if kind == "Normal Odyssey":
            item = build_itemization_row(rec)
            if op == "Create New":
                if rid in existing_items:
                    raise CommitError(f"Create New record already exists: {rid}")
                plan.append_rows.setdefault("Itemization", []).append(item)
                plan.new_normal_record_count += 1
            else:
                if rid not in existing_items:
                    raise CommitError(f"Update Existing Open Record missing from Itemization: {rid}")
                plan.update_rows_by_key.setdefault("Itemization", []).append(("Item ID", rid, item))

            if readiness == "Ready":
                rr = build_ready_row(rec, ready_date)
                if rid in existing_ready:
                    plan.update_rows_by_key.setdefault("Ready for Metadata", []).append(("Item ID", rid, rr))
                else:
                    plan.append_rows.setdefault("Ready for Metadata", []).append(rr)
                    plan.newly_ready_count += 1
            elif readiness.startswith("Blocked"):
                plan.blocked_record_count += 1

        elif kind == "Metadata Only":
            refrow = build_metadata_reference_row(rec)
            if op == "Create New":
                if rid in existing_refs:
                    raise CommitError(f"Create New reference already exists: {rid}")
                plan.append_rows.setdefault("Metadata References", []).append(refrow)
                plan.new_reference_count += 1
            else:
                if rid not in existing_refs:
                    raise CommitError(f"Update Existing reference missing: {rid}")
                plan.update_rows_by_key.setdefault("Metadata References", []).append(("Reference ID", rid, refrow))

    task_rows = build_task_rows(asset_rows, rec_by_id, existing.get("Production Queue", []))
    plan.append_rows["Production Queue"] = task_rows
    plan.new_task_count = len(task_rows)

    open_records = [r for r in record_rows if normalize(r.get("Boundary State After Record")).startswith("Open")]
    if len(open_records) > 1:
        raise CommitError("More than one resulting open intellectual item in one linear batch")
    plan.resulting_open_item = normalize(open_records[0].get("Proposed Record ID")) if open_records else "None"

    plan.validation_notes.extend([
        f"staged_assets={len(asset_rows)}",
        f"new_normal_records={plan.new_normal_record_count}",
        f"new_references={plan.new_reference_count}",
        f"new_tasks={plan.new_task_count}",
        f"newly_ready={plan.newly_ready_count}",
        f"open_item={plan.resulting_open_item}",
    ])
    return plan


# ---------- canonical metrics ----------

def calculate_metrics(tables: Mapping[str, Sequence[Mapping[str, str]]]) -> CommitMetrics:
    asset_qc = tables.get("Asset QC", [])
    itemization = tables.get("Itemization", [])
    refs = tables.get("Metadata References", [])
    ready = tables.get("Ready for Metadata", [])
    tasks = tables.get("Production Queue", [])

    source_assets = len({normalize(r.get("Digital Asset")) for r in asset_qc if normalize(r.get("Digital Asset"))})
    odyssey_records = len({normalize(r.get("Item ID")) for r in itemization if normalize(r.get("Item ID"))})
    metadata_references = len({normalize(r.get("Reference ID")) for r in refs if normalize(r.get("Reference ID"))})
    ready_records = len({normalize(r.get("Item ID")) for r in ready if normalize(r.get("Item ID"))})
    blocked_crop_qc = count_if(itemization, "Metadata Readiness", "Blocked - Cropping/QC")
    blocked_additional_imaging = count_if(itemization, "Metadata Readiness", "Blocked - Additional Imaging")
    review_records = count_if(itemization, "Metadata Readiness", "Review")
    content_linked_assets = sum(parse_int(r.get("Digital Asset Count"), "Digital Asset Count") for r in itemization)
    logical_components = sum(parse_int(r.get("Logical Component Count"), "Logical Component Count") for r in itemization)
    open_tasks = [r for r in tasks if normalize(r.get("Task Status")) == "Open"]
    production_tasks = len(open_tasks)
    additional_imaging_tasks = sum(1 for r in open_tasks if normalize(r.get("Action Needed")) == "Additional Imaging")
    crop_qc_tasks = production_tasks - additional_imaging_tasks
    return CommitMetrics(
        source_assets=source_assets,
        odyssey_records=odyssey_records,
        metadata_references=metadata_references,
        ready_records=ready_records,
        blocked_crop_qc=blocked_crop_qc,
        blocked_additional_imaging=blocked_additional_imaging,
        review_records=review_records,
        content_linked_assets=content_linked_assets,
        logical_components=logical_components,
        production_tasks=production_tasks,
        crop_qc_tasks=crop_qc_tasks,
        additional_imaging_tasks=additional_imaging_tasks,
    )
