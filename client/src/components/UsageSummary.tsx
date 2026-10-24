import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { UsageTotals } from '../lib/types'
import { provenCostLabel, tokenTotalLabel } from '../lib/usageSummary'
import { StatCard } from './ui'

export function UsageSummaryPanel({
  jobId,
  workspaceId,
  project,
  title = 'Usage events',
}: {
  jobId?: string
  workspaceId?: string
  project?: string
  title?: string
}) {
  const [state, setState] = useState('loading')
  const [totals, setTotals] = useState<UsageTotals | null>(null)

  useEffect(() => {
    if (!jobId && !workspaceId && !project) {
      setState('empty')
      setTotals(null)
      return
    }
    let cancelled = false
    setState('loading')
    api.controlUsageSummary({
      job_id: jobId,
      workspace_id: workspaceId,
      project,
    }).then(data => {
      if (cancelled) return
      setState(data.state || 'ready')
      setTotals(data.summary)
    }).catch(() => {
      if (!cancelled) setState('error')
    })
    return () => { cancelled = true }
  }, [jobId, workspaceId, project])

  if (state === 'disabled') {
    return <p className="text-xs text-slate-400">Usage events are off. Google Sheets remains the register.</p>
  }
  if (state === 'empty') {
    return <p className="text-xs text-slate-400">No usage events for this job.</p>
  }
  if (state === 'loading') {
    return <p className="text-xs text-slate-400">Loading usage events</p>
  }
  if (state === 'error' || state === 'offline' || !totals) {
    return <p className="text-xs text-slate-400">Usage event totals could not be loaded. Missing cost stays unknown.</p>
  }

  return (
    <div>
      <div className="mb-3 text-sm font-semibold text-slate-200">{title}</div>
      <p className="mb-3 text-xs text-slate-400">Billing reconciliation is pending. A calculated figure is an application estimate, not an OpenAI charge. Unknown usage is incomplete.</p>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard label="Calculated estimate" value={provenCostLabel(totals)} hint={totals.unknown_event_count ? `${totals.unknown_event_count} unknown` : 'Known categories only'} tone="rose" />
        <StatCard label="Unknown events" value={String(totals.unknown_event_count)} hint="Missing historical cost" tone="amber" />
        <StatCard label="Requests" value={String(totals.request_count)} hint="One count per provider response" tone="cyan" />
        <StatCard label="Cache write" value={tokenTotalLabel(totals.cache_write_tokens, totals.cache_write_known_tokens, totals.cache_write_unknown_count)} hint="Tokens" tone="violet" />
        <StatCard label="Cache read" value={tokenTotalLabel(totals.cache_read_tokens, totals.cache_read_known_tokens, totals.cache_read_unknown_count)} hint="Tokens" tone="violet" />
      </div>
    </div>
  )
}
