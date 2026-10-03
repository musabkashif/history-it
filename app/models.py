from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field, ConfigDict, model_validator

ASSET_HEADERS = ('Project','Job ID','Batch ID','Processing Unit / Source Folder','Asset Sequence','Digital Asset','Box','Source Group / Folder','Assigned Record ID','Asset Role','Required Crop Standard','QC Status','Action Needed','Expected Output Asset Count','QC Confidence','QC Note','Odyssey Ingest / Visibility Note','Production Queue Eligible','Production Task Scope','Production Task Note','Exception / Duplicate Note','Inspection Complete','Commit Status','Commit Note')
RECORD_HEADERS = ('Project','Job ID','Batch ID','Processing Unit / Source Folder','Record Sequence','Proposed Record ID','Record Kind','Record Operation','Box','Source Group / Folder','Parent Scrapbook','Parent Scrapbook Page','New Batch Assets','Existing Committed Assets','Full Record Assets After Commit','Digital Asset Count','Logical Component Count','Template','Type','Reference Role','Further Detail','Required Crop Standard','Record QC Status','Record Action Needed','Metadata Readiness','Confidence','Review Note','Production Task Count','Odyssey Ingest / Visibility Note','Notes','Boundary State After Record','Inspection Complete','Commit Status','Commit Note')

class StrictRow(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

class AssetRow(StrictRow):
    project: str = Field(alias='Project')
    job_id: str = Field(alias='Job ID')
    batch_id: str = Field(alias='Batch ID')
    processing_unit: str = Field(alias='Processing Unit / Source Folder')
    asset_sequence: str = Field(alias='Asset Sequence')
    digital_asset: str = Field(alias='Digital Asset')
    box: str = Field(alias='Box')
    source_group: str = Field(alias='Source Group / Folder')
    assigned_record_id: str = Field(alias='Assigned Record ID')
    asset_role: str = Field(alias='Asset Role')
    required_crop_standard: str = Field(alias='Required Crop Standard')
    qc_status: str = Field(alias='QC Status')
    action_needed: str = Field(alias='Action Needed')
    expected_output_asset_count: str = Field(alias='Expected Output Asset Count')
    qc_confidence: str = Field(alias='QC Confidence')
    qc_note: str = Field(alias='QC Note')
    odyssey_note: str = Field(alias='Odyssey Ingest / Visibility Note')
    queue_eligible: str = Field(alias='Production Queue Eligible')
    task_scope: str = Field(alias='Production Task Scope')
    task_note: str = Field(alias='Production Task Note')
    exception_note: str = Field(alias='Exception / Duplicate Note')
    inspection_complete: str = Field(alias='Inspection Complete')
    commit_status: str = Field(alias='Commit Status')
    commit_note: str = Field(alias='Commit Note')

class RecordRow(StrictRow):
    project: str = Field(alias='Project')
    job_id: str = Field(alias='Job ID')
    batch_id: str = Field(alias='Batch ID')
    processing_unit: str = Field(alias='Processing Unit / Source Folder')
    record_sequence: str = Field(alias='Record Sequence')
    proposed_record_id: str = Field(alias='Proposed Record ID')
    record_kind: str = Field(alias='Record Kind')
    record_operation: str = Field(alias='Record Operation')
    box: str = Field(alias='Box')
    source_group: str = Field(alias='Source Group / Folder')
    parent_scrapbook: str = Field(alias='Parent Scrapbook')
    parent_scrapbook_page: str = Field(alias='Parent Scrapbook Page')
    new_batch_assets: str = Field(alias='New Batch Assets')
    existing_committed_assets: str = Field(alias='Existing Committed Assets')
    full_record_assets: str = Field(alias='Full Record Assets After Commit')
    digital_asset_count: str = Field(alias='Digital Asset Count')
    logical_component_count: str = Field(alias='Logical Component Count')
    template: str = Field(alias='Template')
    type: str = Field(alias='Type')
    reference_role: str = Field(alias='Reference Role')
    further_detail: str = Field(alias='Further Detail')
    required_crop_standard: str = Field(alias='Required Crop Standard')
    record_qc_status: str = Field(alias='Record QC Status')
    record_action_needed: str = Field(alias='Record Action Needed')
    metadata_readiness: str = Field(alias='Metadata Readiness')
    confidence: str = Field(alias='Confidence')
    review_note: str = Field(alias='Review Note')
    production_task_count: str = Field(alias='Production Task Count')
    odyssey_note: str = Field(alias='Odyssey Ingest / Visibility Note')
    notes: str = Field(alias='Notes')
    boundary_state: str = Field(alias='Boundary State After Record')
    inspection_complete: str = Field(alias='Inspection Complete')
    commit_status: str = Field(alias='Commit Status')
    commit_note: str = Field(alias='Commit Note')

class InspectionOutput(StrictRow):
    assets: list[AssetRow]
    records: list[RecordRow]

class Source(StrictRow):
    id: str
    name: str
    folder_id: str
    folder_name: str
    path: str
    mime_type: str

class JobCreate(BaseModel):
    project: str
    source_folder_id: str
    spreadsheet_id: str
    batch_size: int | None = Field(default=None, ge=1, le=100)
    box: str = 'Chapters Box 19'

class JobState(BaseModel):
    job_id: str
    project: str
    spreadsheet_id: str
    source_folder_id: str
    batches: int
    status: str
    next_batch: str | None = None
    alert: str | None = None

class BenchmarkRequest(BaseModel):
    spreadsheet_id: str
    benchmark_spreadsheet_id: str | None = None
    box: str = 'Chapters Box 19'
