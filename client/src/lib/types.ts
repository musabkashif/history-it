export type JobStatus = string

export interface Health {
  status: string
  service?: string
  configured?: boolean
  diagnostics_build?: string
  login_configured?: boolean
  developer_verification?: boolean
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
