export type ControlView = 'loading' | 'disabled' | 'offline' | 'error' | 'empty' | 'ready'

export function controlView(input: {
  loading: boolean
  requestFailed?: boolean
  offline?: boolean
  disabled?: boolean
  empty?: boolean
}): ControlView {
  if (input.loading) return 'loading'
  if (input.offline) return 'offline'
  if (input.requestFailed) return 'error'
  if (input.disabled) return 'disabled'
  if (input.empty) return 'empty'
  return 'ready'
}

export function controlMessage(state: ControlView): string {
  switch (state) {
    case 'loading':
      return 'Loading control-plane records…'
    case 'disabled':
      return 'The database control plane is off. Google Sheets is still the working record.'
    case 'offline':
      return 'The control database is unavailable. Google Sheets workflow is still usable.'
    case 'error':
      return 'The control plane could not load records.'
    case 'empty':
      return 'No records yet.'
    default:
      return ''
  }
}

export function shown(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return 'unknown'
  const text = String(value).trim()
  return text || 'unknown'
}

export function jobScreen(source: string | null | undefined): 'sheets' | 'record' {
  return source === 'live' ? 'sheets' : 'record'
}

export function sourceLabel(source: string | null | undefined): string {
  if (source === 'historical') return 'Historical'
  if (source === 'imported') return 'Imported'
  if (source === 'live') return 'Live'
  return 'unknown'
}

export function resumeAllowed(job: { source?: string | null; job_status?: string | null; resume_allowed?: boolean }): boolean {
  if (typeof job.resume_allowed === 'boolean') return job.resume_allowed
  return job.source === 'live' && (job.job_status === 'paused' || job.job_status === 'failed')
}

export function sectionLabel(count: number, noun: string): string | null {
  if (count === 0) return `No ${noun} recorded.`
  return null
}

export function databaseWarning(health: { database_message?: string | null; database_control_plane_enabled?: boolean; database_available?: boolean | null; status?: string | null } | null): string | null {
  if (!health || health.status === 'offline') return null
  const message = health.database_message || ''
  if (/unavailable|invalid|production environment/i.test(message)) return message
  if (health.database_control_plane_enabled && health.database_available === false) {
    return 'The control database is unavailable. Google Sheets workflow is still usable.'
  }
  return null
}
