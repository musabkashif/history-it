import type { BatchesResponse, CheckpointResponse, CompareResponse, Health, JobForm, JobState, UsageResponse, ValidationResponse } from './types'

export const config = {
  baseUrl: (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, ''),
}

const CSRF_COOKIE = 'historyit_csrf'
const CSRF_HEADER = 'X-CSRF-Token'

function readCookie(name: string): string {
  const parts = document.cookie.split(';')
  for (const part of parts) {
    const [rawKey, ...rest] = part.trim().split('=')
    if (rawKey === name) return decodeURIComponent(rest.join('=') || '')
  }
  return ''
}

function isMutating(method?: string) {
  const verb = (method || 'GET').toUpperCase()
  return !['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(verb)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')
  if (isMutating(init.method)) {
    const csrf = readCookie(CSRF_COOKIE)
    if (csrf) headers.set(CSRF_HEADER, csrf)
  }
  let response: Response
  try {
    response = await fetch(`${config.baseUrl}${path}`, { ...init, headers, credentials: 'include' })
  } catch {
    throw new Error('Could not reach the archive service.')
  }
  const text = await response.text()
  let data: unknown = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!response.ok) {
    const detail = typeof data === 'object' && data && 'detail' in data ? String((data as { detail: unknown }).detail) : `${response.status} ${response.statusText}`
    throw new ApiError(response.status, detail)
  }
  return data as T
}

export type SessionResponse = { authenticated: boolean; username?: string }

export const api = {
  health: () => request<Health>('/health'),
  login: (username: string, password: string) => request<SessionResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  session: () => request<SessionResponse>('/api/auth/session'),
  logout: () => request<SessionResponse>('/api/auth/logout', { method: 'POST' }),
  createJob: (body: JobForm) => request<JobState>('/api/jobs', { method: 'POST', body: JSON.stringify({ ...body, batch_size: body.batch_size || undefined }) }),
  getJob: (id: string) => request<JobState>(`/api/jobs/${encodeURIComponent(id)}`),
  getBatches: (id: string) => request<BatchesResponse>(`/api/jobs/${encodeURIComponent(id)}/batches`),
  getUsage: (id: string) => request<UsageResponse>(`/api/jobs/${encodeURIComponent(id)}/usage`),
  getValidation: (id: string) => request<ValidationResponse>(`/api/jobs/${encodeURIComponent(id)}/validation`),
  start: (id: string) => request<JobState>(`/api/jobs/${encodeURIComponent(id)}/start`, { method: 'POST' }),
  pause: (id: string) => request<JobState>(`/api/jobs/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  resume: (id: string) => request<JobState>(`/api/jobs/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  reconcile: (id: string) => request<Record<string, unknown>>(`/api/jobs/${encodeURIComponent(id)}/reconcile`, { method: 'POST' }),
  validate: (id: string) => request<Record<string, unknown>>(`/api/jobs/${encodeURIComponent(id)}/validate`, { method: 'POST' }),
  retry: (batchId: string) => request<Record<string, unknown>>(`/api/batches/${encodeURIComponent(batchId)}/retry`, { method: 'POST' }),
  authorizeFolder: (id: string, source_folder_id: string, batch_size?: number) => request<JobState>(`/api/jobs/${encodeURIComponent(id)}/folders`, { method: 'POST', body: JSON.stringify({ source_folder_id, batch_size: batch_size || undefined }) }),
  checkpoint: (spreadsheet_id: string) => request<CheckpointResponse>('/api/benchmark/f01-checkpoint', { method: 'POST', body: JSON.stringify({ spreadsheet_id }) }),
  compare: (body: { spreadsheet_id: string; benchmark_spreadsheet_id?: string; box?: string; folder?: string }) => request<CompareResponse>('/api/benchmark/compare', { method: 'POST', body: JSON.stringify(body) }),
}
