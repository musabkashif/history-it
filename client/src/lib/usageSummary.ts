import type { UsageTotals } from './types'

export function provenCostLabel(totals: UsageTotals | null | undefined): string {
  if (!totals || totals.request_count === 0) return 'No usage events'
  if (totals.proven_cost_usd == null || totals.proven_cost_usd === '') return 'Unknown'
  const amount = Number(totals.proven_cost_usd)
  if (!Number.isFinite(amount)) return 'Unknown'
  return `$${amount.toFixed(2)} estimate`
}

export function tokenTotalLabel(complete: number | null, known: number | null, unknown: number): string {
  if (complete != null) return complete.toLocaleString()
  if (known != null && unknown > 0) return `${known.toLocaleString()} known · ${unknown} unknown`
  if (unknown > 0) return 'Unknown'
  return '—'
}

export function usageSummaryLine(totals: UsageTotals | null | undefined): string {
  if (!totals || totals.request_count === 0) return 'No usage events'
  const proven = provenCostLabel(totals)
  const unknown = totals.unknown_event_count
  return `${proven} · ${unknown} unknown · ${totals.request_count} requests`
}
