/** Shared date/time helpers for project and job activity metadata. */

const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

const ABSOLUTE_FORMATTER = new Intl.DateTimeFormat('en', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

export type ParsedInstant = {
  date: Date
  iso: string
  valid: true
} | {
  date: null
  iso: ''
  valid: false
  reason: 'missing' | 'invalid'
}

export function parseInstant(value: string | number | Date | null | undefined, now = Date.now()): ParsedInstant {
  if (value == null || value === '') return { date: null, iso: '', valid: false, reason: 'missing' }
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value)
  if (Number.isNaN(date.getTime())) return { date: null, iso: '', valid: false, reason: 'invalid' }
  // Extremely far-future values are treated as invalid for display safety.
  if (date.getTime() - now > 1000 * 60 * 60 * 24 * 365 * 50) {
    return { date: null, iso: '', valid: false, reason: 'invalid' }
  }
  return { date, iso: date.toISOString(), valid: true }
}

export function formatExactTimestamp(value: string | number | Date | null | undefined, now = Date.now()): string {
  const parsed = parseInstant(value, now)
  if (!parsed.valid || !parsed.date) return ''
  return ABSOLUTE_FORMATTER.format(parsed.date)
}

export function formatRelativeTimestamp(value: string | number | Date | null | undefined, now = Date.now()): string {
  const parsed = parseInstant(value, now)
  if (!parsed.valid || !parsed.date) return ''
  const deltaMs = parsed.date.getTime() - now
  const absSeconds = Math.round(Math.abs(deltaMs) / 1000)
  const sign = deltaMs <= 0 ? -1 : 1
  if (absSeconds < 45) return 'just now'
  const absMinutes = Math.round(absSeconds / 60)
  if (absMinutes < 60) return RELATIVE_FORMATTER.format(sign * absMinutes, 'minute')
  const absHours = Math.round(absMinutes / 60)
  if (absHours < 36) return RELATIVE_FORMATTER.format(sign * absHours, 'hour')
  const absDays = Math.round(absHours / 24)
  if (absDays < 30) return RELATIVE_FORMATTER.format(sign * absDays, 'day')
  const absMonths = Math.round(absDays / 30)
  if (absMonths < 18) return RELATIVE_FORMATTER.format(sign * absMonths, 'month')
  const absYears = Math.round(absDays / 365)
  return RELATIVE_FORMATTER.format(sign * absYears, 'year')
}

export function activityPhrase(
  label: 'Created' | 'Updated' | 'Last activity' | 'Last status change',
  value: string | number | Date | null | undefined,
  now = Date.now(),
): { text: string; title: string } | null {
  const relative = formatRelativeTimestamp(value, now)
  const exact = formatExactTimestamp(value, now)
  if (!relative || !exact) return null
  return {
    text: `${label} ${relative}`,
    title: `${label} ${exact}`,
  }
}

export function pickJobActivity(job: {
  created_at?: string | null
  updated_at?: string | null
  started_at?: string | null
  completed_at?: string | null
}): { created?: string | null; activity?: string | null; activityLabel: 'Updated' | 'Last activity' | 'Last status change' } {
  const created = job.created_at || null
  if (job.updated_at) return { created, activity: job.updated_at, activityLabel: 'Updated' }
  if (job.completed_at) return { created, activity: job.completed_at, activityLabel: 'Last activity' }
  if (job.started_at) return { created, activity: job.started_at, activityLabel: 'Last activity' }
  return { created, activity: null, activityLabel: 'Updated' }
}
