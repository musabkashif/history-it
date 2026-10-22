export type JobStatus = string

export interface Health {
  status: string
  service?: string
  configured?: boolean
  diagnostics_build?: string
  login_configured?: boolean
  developer_verification?: boolean
  database_configured?: boolean
  database_available?: boolean | null
  database_control_plane_enabled?: boolean
  database_shadow_writes_enabled?: boolean
  database_backfill_enabled?: boolean
  database_message?: string
}
export interface JobState {
  job_id: string; project: string; spreadsheet_id: string; source_folder_id: string
  authorized_source_folder_ids: string[]; discovered_folders?: string[]; batches: number; status: string
  next_batch?: string | null; current_folder?: string | null; current_batch?: string | null; phase?: string | null
  running?: boolean; already_running?: boolean; pause_requested?: boolean; authorized_scope_complete?: boolean
  alert?: string | null
}
export interface Batch {
  'Batch ID': string; 'Source Group / Folder'?: string; 'Source Folder URL'?: string; 'Start Filename'?: string; 'End Filename'?: string
  'Planned Asset Count'?: string | number; 'Batch Status'?: string; 'Prior Open Item'?: string; 'Resulting Open Item'?: string
  'Inspection Attempt'?: string | number; 'Commit Attempt'?: string | number; 'Validation Status'?: string; 'Error / Retry Note'?: string
  'Asset Staging Row Range'?: string; 'Record Staging Row Range'?: string; 'Next Batch Start'?: string; 'Staging Schema'?: string
  [key: string]: unknown
}
export interface UsageRow { 'Call ID'?: string; 'Batch ID'?: string; Model?: string; 'Source Image Count'?: string | number; 'Input Tokens'?: string | number; 'Cached Input Tokens'?: string | number; 'Output Tokens'?: string | number; 'Estimated Cost USD'?: string | number; Attempt?: string | number; 'Elapsed Seconds'?: string | number; Status?: string; [key: string]: unknown }
export interface UsageResponse { job_id: string; usage: UsageRow[]; total_cost_usd?: number; alert?: string | null }
export interface BatchesResponse { job_id: string; batches: Batch[]; alert?: string | null }
export interface ValidationResponse { job_id: string; validation: Record<string, unknown>[] }
export interface CheckpointResponse { outcome: string; matches: boolean; final_box19_match?: boolean; counts?: { actual?: Record<string, number>; expected?: Record<string, number>; differences?: Record<string, unknown> }; checks?: Record<string, boolean>; [key: string]: unknown }
export interface CompareResponse { outcome: string; matches: boolean; strict_outcome?: string; archival_outcome?: string; archival_matches?: boolean; archival_policy?: string; archival_differences?: Record<string, unknown>[]; archival_match?: Record<string, { matched: number; total: number; percent: number }>; final_box19_match?: boolean; internally_consistent?: boolean; controlled_mismatch?: boolean; free_text_mismatch?: boolean; counts?: { live?: Record<string, number>; benchmark?: Record<string, number>; differences?: Record<string, unknown> }; difference_classes?: Record<string, unknown>[]; sheets?: Record<string, { live_count?: number; benchmark_count?: number; matches?: boolean; missing?: string[]; unexpected?: string[]; value_mismatches?: { key: string; fields?: Record<string, unknown>; field_classes?: Record<string, string> }[] }>; f01_letter_transition?: string; [key: string]: unknown }
export interface JobForm { project: string; source_folder_id: string; spreadsheet_id: string; batch_size: number; box: string }

export interface ControlWorkspace {
  workspace_id: string
  project_name?: string | null
  client_organization?: string | null
  source_root_url?: string | null
  source_root_id?: string | null
  default_register_url?: string | null
  register_id?: string | null
  environment?: string | null
  status?: string | null
  created_at?: string | null
  updated_at?: string | null
}
export interface ControlSummaryCounts {
  asset_count?: number | null
  batch_count?: number | null
  folder_count?: number | null
  completed_batch_count?: number | null
  failed_batch_count?: number | null
  last_batch_asset_count?: number | null
}
export interface ControlJob {
  job_id: string
  workspace_id: string
  project_name?: string | null
  box_label?: string | null
  parent_folder_url?: string | null
  parent_folder_id?: string | null
  register_url?: string | null
  register_id?: string | null
  register_file_reference?: string | null
  batch_size?: number | null
  job_status?: string | null
  sheets_status?: string | null
  source?: string | null
  summary_origin?: string | null
  created_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  updated_at?: string | null
  deployed_release?: string | null
  summary_counts?: ControlSummaryCounts | null
  last_error?: string | null
  completion_evidence?: string | null
  reopen_allowed?: boolean
  sheets_reopen_allowed?: boolean
  resume_allowed?: boolean
  pause_allowed?: boolean
  retry_allowed?: boolean
  start_allowed?: boolean
}
export interface ControlListResponse<T> {
  state: string
  enabled: boolean
  available: boolean
  warning?: string | null
  inspection_started?: boolean
  workspaces?: T[]
  jobs?: T[]
}
export interface ControlFolder {
  folder_id: string
  folder_url?: string | null
  folder_name?: string | null
  folder_identifier_source?: string | null
  box_label?: string | null
  discovery_order?: number | null
  authorization_status?: string | null
  processing_status?: string | null
  asset_count?: number | null
  completed_at?: string | null
}
export interface ControlBatch {
  batch_id: string
  folder_id?: string | null
  batch_number?: number | null
  asset_count?: number | null
  start_filename?: string | null
  end_filename?: string | null
  status?: string | null
  attempt_count?: number | null
  validation_status?: string | null
  commit_status?: string | null
  prior_open_item?: string | null
  resulting_open_item?: string | null
  error_information?: string | null
  response_ids?: string[]
  lease_owner?: string | null
}
export interface ControlEvent {
  event_id: string
  event_type?: string | null
  previous_status?: string | null
  new_status?: string | null
  message?: string | null
  release?: string | null
  actor?: string | null
  timestamp?: string | null
  folder_id?: string | null
  batch_id?: string | null
}
export interface ControlCheckpoint {
  checkpoint_id: string
  current_state?: string | null
  last_successful_step?: string | null
  pause_state?: Record<string, unknown> | null
  resume_state?: Record<string, unknown> | null
  checkpoint_version?: number | null
  created_at?: string | null
  folder_id?: string | null
  batch_id?: string | null
}
export interface ControlSnapshot {
  snapshot_id: string
  register_url?: string | null
  register_id?: string | null
  source_folder?: string | null
  counts?: Record<string, unknown>
  validation_result?: string | null
  validation_results?: { check_id?: string | null; scope?: string | null; result?: string | null }[]
  continuation?: Record<string, string | null>
  box_status?: string | null
  readback_checksum?: string | null
  response_ids?: string[]
  captured_at?: string | null
}
export interface ControlJobDetail {
  state: string
  enabled: boolean
  available: boolean
  warning?: string | null
  inspection_started?: boolean
  already_applied?: boolean
  warnings?: string[]
  job: ControlJob
  workspace?: ControlWorkspace | null
  folders: ControlFolder[]
  batches: ControlBatch[]
  checkpoints: ControlCheckpoint[]
  events: ControlEvent[]
  snapshots: ControlSnapshot[]
  validation_results: { check_id?: string | null; scope?: string | null; result?: string | null }[]
  errors: { source?: string; batch_id?: string; message?: string | null; timestamp?: string | null }[]
  retries: ControlEvent[]
  register?: { register_url?: string | null; register_id?: string | null; register_file_reference?: string | null }
}
export interface ControlJobFilters {
  workspace_id?: string
  project?: string
  box?: string
  status?: string
  source?: string
  created_from?: string
  created_to?: string
}
export interface ControlImportRequest {
  job_id: string
  register_url?: string
  register_file_reference?: string
  source: 'historical' | 'imported'
  deployed_release?: string
}

/**
 * True only when the backend worker thread is admitted/alive, or the API already
 * returned a Running-* label for that live worker.
 *
 * Idle "Authorized / Pending Inspection" is NOT running — operators must be able
 * to press Start. Do not match bare "Pending" or bare "Inspection".
 */
export function jobIsRunning(job: JobState | null | undefined): boolean {
  if (!job) return false
  if (job.running) return true
  return /^Running(\b| -)/i.test(String(job.status || '').trim())
}

/** Poll while the worker is live (or a Start request is still in flight). */
export function jobIsActive(job: JobState | null | undefined, starting = false): boolean {
  return starting || jobIsRunning(job)
}

/** Batch/job phases that Start (or Resume) may advance when no worker is live. */
export function isStartableStatus(status: string | null | undefined): boolean {
  const text = String(status || '').trim()
  return /Authorized\s*\/\s*Pending Inspection|Inspection Complete - Awaiting Commit|Commit Written - Pending Validation|Inspection Failed - Retryable|Commit Failed - Retryable|^Paused$/i.test(text)
}

export function jobIsStartable(job: JobState | null | undefined, pendingBatchStatus?: string | null): boolean {
  if (!job || jobIsRunning(job)) return false
  return isStartableStatus(pendingBatchStatus || job.phase || job.status)
}

export function displayJobStatus(job: JobState, starting = false): string {
  if (starting) return 'Starting…'
  if (jobIsRunning(job)) {
    const phase = String(job.phase || '')
    if (/Commit Written - Pending Validation/i.test(phase)) return 'Running - Validating'
    if (/Inspection Complete - Awaiting Commit/i.test(phase)) return 'Running - Committing'
    if (/Inspection In Progress/i.test(phase)) return 'Running - Inspecting'
    if (/^Running(\b| -)/i.test(String(job.status || ''))) return job.status
    return 'Running'
  }
  return job.status
}
