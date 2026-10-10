export type JobStatus = string

export interface Health { status: string; service?: string; configured?: boolean; diagnostics_build?: string }
export interface JobState {
  job_id: string; project: string; spreadsheet_id: string; source_folder_id: string
  authorized_source_folder_ids: string[]; batches: number; status: string; next_batch?: string | null; alert?: string | null
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
export interface CompareResponse { outcome: string; matches: boolean; final_box19_match?: boolean; internally_consistent?: boolean; controlled_mismatch?: boolean; free_text_mismatch?: boolean; counts?: { live?: Record<string, number>; benchmark?: Record<string, number>; differences?: Record<string, unknown> }; difference_classes?: Record<string, unknown>[]; sheets?: Record<string, { live_count?: number; benchmark_count?: number; matches?: boolean; missing?: string[]; unexpected?: string[]; value_mismatches?: { key: string; fields?: Record<string, unknown>; field_classes?: Record<string, string> }[] }>; f01_letter_transition?: string; [key: string]: unknown }
export interface JobForm { project: string; source_folder_id: string; spreadsheet_id: string; batch_size: number; box: string }
