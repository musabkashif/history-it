"""Strict control-plane documents.

Stored records use null when the older register did not record a value.
Action flags are derived when a job is presented; they are not persisted.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    paused = 'paused'
    blocked = 'blocked'
    validated = 'validated'
    failed = 'failed'
    completed = 'completed'
    unknown = 'unknown'


class JobSource(str, Enum):
    live = 'live'
    imported = 'imported'
    historical = 'historical'


class SummaryCounts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_count: int | None = None
    batch_count: int | None = None
    folder_count: int | None = None
    completed_batch_count: int | None = None
    failed_batch_count: int | None = None
    last_batch_asset_count: int | None = None


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str
    project_name: str | None = None
    client_organization: str | None = None
    source_root_url: str | None = None
    source_root_id: str | None = None
    default_register_url: str | None = None
    register_id: str | None = None
    environment: str
    status: str
    created_at: str
    updated_at: str
    version: int = 1


class JobRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str
    workspace_id: str
    project_name: str | None = None
    box_label: str | None = None
    parent_folder_url: str | None = None
    parent_folder_id: str | None = None
    register_url: str | None = None
    register_id: str | None = None
    register_file_reference: str | None = None
    batch_size: int | None = None
    job_status: JobStatus
    sheets_status: str | None = None
    source: JobSource
    summary_origin: Literal['register', 'operator_notes', 'unknown'] = 'unknown'
    created_by: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    deployed_release: str | None = None
    summary_counts: SummaryCounts = Field(default_factory=SummaryCounts)
    last_error: str | None = None
    last_checkpoint_id: str | None = None
    completion_evidence: str | None = None
    version: int = 1


class FolderRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str
    job_id: str
    folder_id: str
    folder_url: str | None = None
    folder_name: str | None = None
    folder_identifier_source: Literal['drive_url', 'folder_name', 'unknown']
    box_label: str | None = None
    discovery_order: int | None = None
    authorization_status: str | None = None
    processing_status: str | None = None
    asset_count: int | None = None
    completed_at: str | None = None
    version: int = 1


class BatchRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workspace_id: str
    job_id: str
    folder_id: str
    batch_id: str
    batch_number: int | None = None
    asset_count: int | None = None
    start_filename: str | None = None
    end_filename: str | None = None
    status: str | None = None
    attempt_count: int | None = None
    inspection_attempt: int | None = None
    commit_attempt: int | None = None
    validation_status: str | None = None
    commit_status: str | None = None
    prior_open_item: str | None = None
    resulting_open_item: str | None = None
    error_information: str | None = None
    response_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    version: int = 1
    lease_owner: str | None = None
    lease_until: str | None = None


class CheckpointRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    checkpoint_id: str
    job_id: str
    workspace_id: str
    folder_id: str | None = None
    batch_id: str | None = None
    current_state: str
    last_successful_step: str | None = None
    pause_state: dict | None = None
    resume_state: dict | None = None
    checkpoint_version: int
    created_at: str


class EventRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str
    workspace_id: str
    job_id: str
    folder_id: str | None = None
    batch_id: str | None = None
    event_type: str
    previous_status: str | None = None
    new_status: str | None = None
    message: str | None = None
    release: str | None = None
    actor: str | None = None
    timestamp: str
    correlation_id: str | None = None


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snapshot_id: str
    job_id: str
    workspace_id: str
    register_url: str | None = None
    register_id: str | None = None
    source_folder: str | None = None
    counts: dict
    validation_result: str | None = None
    validation_results: list[dict] = Field(default_factory=list)
    continuation: dict[str, str | None] = Field(default_factory=dict)
    box_status: str | None = None
    readback_checksum: str | None = None
    response_ids: list[str] = Field(default_factory=list)
    captured_at: str


class PlanFolder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    folder_id: str
    folder_url: str | None = None
    folder_name: str | None = None
    folder_identifier_source: Literal['drive_url', 'folder_name', 'unknown']
    box_label: str | None = None
    discovery_order: int | None = None
    authorization_status: str | None = None
    processing_status: str | None = None
    asset_count: int | None = None
    completed_at: str | None = None


class PlanBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    folder_id: str
    batch_id: str
    batch_number: int | None = None
    asset_count: int | None = None
    start_filename: str | None = None
    end_filename: str | None = None
    status: str | None = None
    attempt_count: int | None = None
    inspection_attempt: int | None = None
    commit_attempt: int | None = None
    validation_status: str | None = None
    commit_status: str | None = None
    prior_open_item: str | None = None
    resulting_open_item: str | None = None
    error_information: str | None = None
    response_ids: list[str] = Field(default_factory=list)
    completed_at: str | None = None


class PlanSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    register_url: str | None = None
    register_id: str | None = None
    source_folder: str | None = None
    counts: dict
    validation_result: str | None = None
    validation_results: list[dict] = Field(default_factory=list)
    continuation: dict[str, str | None] = Field(default_factory=dict)
    box_status: str | None = None
    readback_checksum: str | None = None
    response_ids: list[str] = Field(default_factory=list)


class ImportPlan(BaseModel):
    """A register extract ready to store. inspection_started is always false."""

    model_config = ConfigDict(extra='forbid')
    job_id: str
    project_name: str | None = None
    client_organization: str | None = None
    box_label: str | None = None
    parent_folder_url: str | None = None
    parent_folder_id: str | None = None
    register_url: str | None = None
    register_id: str | None = None
    register_file_reference: str | None = None
    batch_size: int | None = None
    job_status: JobStatus = JobStatus.unknown
    sheets_status: str | None = None
    source: JobSource = JobSource.imported
    summary_origin: Literal['register', 'operator_notes', 'unknown'] = 'unknown'
    deployed_release: str | None = None
    summary_counts: SummaryCounts = Field(default_factory=SummaryCounts)
    last_error: str | None = None
    completion_evidence: str | None = None
    completed_at: str | None = None
    folders: list[PlanFolder] = Field(default_factory=list)
    batches: list[PlanBatch] = Field(default_factory=list)
    snapshot: PlanSnapshot | None = None
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None
    inspection_started: bool = False

    @model_validator(mode='after')
    def _never_start_inspection(self):
        if self.inspection_started:
            raise ValueError('Import cannot start inspection.')
        return self
