import { ArrowUpRight, CheckCircle2, Clock3, FolderOpen, Image, Play, RefreshCw, ShieldCheck, WalletCards } from 'lucide-react'
import React from 'react'
import type { Batch, JobState, UsageResponse } from '../lib/types'
import { displayJobStatus, jobIsRunning } from '../lib/types'
import { Button, EmptyState, SectionHeader, StatCard, StatusBadge } from '../components/ui'
import { UsageSummaryPanel } from '../components/UsageSummary'

export function Overview({
  job, batches, usage, onNavigate, onRefresh, loading, lastRefreshAt, refreshError, developerVerification,
}: {
  job: JobState | null
  batches: Batch[]
  usage: UsageResponse | null
  onNavigate: (v: 'new-job'|'job'|'benchmark'|'settings'|'projects') => void
  onRefresh: () => void
  loading?: boolean
  lastRefreshAt?: string | null
  refreshError?: string | null
  developerVerification?: boolean
}) {
  const committed = batches.filter(b => /Committed - Validated/i.test(String(b['Batch Status']))).length
  const totalAssets = batches.reduce((sum, b) => sum + Number(b['Planned Asset Count'] || 0), 0)
  const failed = batches.filter(b => /Failed|Blocked/i.test(String(b['Batch Status']))).length
  const recorded = usage?.total_cost_usd
  const recordedAmount = recorded == null || recorded === '' ? null : Number(recorded)
  const costLabel = recordedAmount == null || !Number.isFinite(recordedAmount) ? 'Unknown' : `$${recordedAmount.toFixed(2)}`
  const statusLabel = job ? displayJobStatus(job) : ''
  const discovered = job?.discovered_folders?.length ? job.discovered_folders : []
  const discoveredHint = discovered.length
    ? discovered.join(', ')
    : 'Authorize a parent box to discover F-folders'

  return (
    <>
      <SectionHeader
        eyebrow="Odyssey / control room"
        title={job?.project || 'Archive console'}
        description="Run, monitor, and validate the archive itemization pipeline from one operator console."
        actions={<Button tone="ghost" onClick={onRefresh} loading={loading}><RefreshCw className="h-4 w-4" /> Refresh</Button>}
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Job status" value={job ? <StatusBadge status={statusLabel} /> : 'Not connected'} hint={job ? job.job_id : 'Create or open a job'} icon={<ShieldCheck className="h-5 w-5" />} tone="cyan" />
        <StatCard label="Batches committed" value={`${committed}/${batches.length || 0}`} hint={failed ? `${failed} needs attention` : 'Validated readback'} icon={<CheckCircle2 className="h-5 w-5" />} tone="violet" />
        <StatCard label="Manifest assets" value={totalAssets || '—'} hint="Authorized source images" icon={<Image className="h-5 w-5" />} tone="amber" />
        <StatCard label="Recorded usage" value={costLabel} hint="Register estimate. Billing reconciliation is pending." icon={<WalletCards className="h-5 w-5" />} tone="rose" />
      </div>
      {job && <div className="mt-6"><UsageSummaryPanel jobId={job.job_id} title="Usage events for this job" /></div>}
      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_.8fr]">
        <div className="surface p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="eyebrow">Current job</div>
              <h2 className="mt-2 text-lg font-semibold text-white">{job?.project || 'No job selected'}</h2>
              <p className="mt-1 text-xs text-slate-400">{job ? `${job.job_id} · ${job.spreadsheet_id}` : 'Create a job or open an existing Job ID in Settings.'}</p>
            </div>
            {job && <StatusBadge status={statusLabel} />}
          </div>
          {job ? (
            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-line/50 bg-white/[.02] p-4">
                <div className="text-xs text-slate-400">Discovered folders</div>
                <div className="mt-2 text-xl font-semibold text-white">{discovered.length || job.authorized_source_folder_ids?.length || 0}</div>
                <div className="mt-1 text-[11px] text-slate-300">{discoveredHint}</div>
              </div>
              <div className="rounded-xl border border-line/50 bg-white/[.02] p-4">
                <div className="text-xs text-slate-400">Next action</div>
                <div className="mt-2 truncate text-sm font-semibold text-slate-100">{jobIsRunning(job) ? (job.phase || 'Running') : (job.next_batch || (job.authorized_scope_complete ? 'Authorized scope complete' : 'All batches validated'))}</div>
              </div>
              <div className="rounded-xl border border-line/50 bg-white/[.02] p-4">
                <div className="text-xs text-slate-400">Source folder</div>
                <div className="mt-2 truncate text-sm font-semibold text-slate-100">{job.source_folder_id}</div>
                {lastRefreshAt && <div className="mt-1 text-[11px] text-slate-300">Refreshed {new Date(lastRefreshAt).toLocaleTimeString()}</div>}
                {refreshError && <div className="mt-1 text-[11px] text-amber">Refresh issue (worker not assumed stopped)</div>}
              </div>
            </div>
          ) : (
            <EmptyState
              title="No job loaded"
              body="Create a new job against a clean Developer Test register or open an existing Job ID."
              action={<Button tone="primary" onClick={() => onNavigate('new-job')}><Play className="h-4 w-4" /> Create a job</Button>}
            />
          )}
        </div>
        <div className="surface p-6">
          <div className="eyebrow">Next steps</div>
          <h2 className="mt-2 text-lg font-semibold text-white">Keep the handoff clear</h2>
          <div className="mt-5 space-y-4">
            <Action icon={<FolderOpen />} title="Browse projects" body="Open a project to see its jobs, create a job, or review a historical record." onClick={() => onNavigate('projects')} />
            <Action icon={<FolderOpen />} title="Process authorized scope" body="One Start advances every queued batch and discovered F-folder on the backend." onClick={() => onNavigate('job')} />
            <Action icon={<Clock3 />} title="Review evidence" body="Inspect batch statuses, validation checks, usage, and diagnostics." onClick={() => onNavigate('job')} />
            <Action icon={<ShieldCheck />} title="Operator guide" body="Drive Viewer, Sheets Editor, links/IDs, Start/Pause, and recovery." onClick={() => onNavigate('settings')} />
            {developerVerification && (
              <Action icon={<FolderOpen />} title="Developer: Tri Delta Box 19 verification" body="Calibration checkpoint and Frozen Benchmark harness only — not generic project verification." onClick={() => onNavigate('benchmark')} />
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function Action({ icon, title, body, onClick }: { icon: React.ReactNode; title: string; body: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="group flex w-full items-start gap-3 text-left">
      <div className="rounded-xl border border-line/50 bg-white/[.03] p-2.5 text-cyan transition group-hover:border-cyan/40">
        {React.cloneElement(icon as React.ReactElement, { className: 'h-4 w-4' })}
      </div>
      <div className="min-w-0">
        <div className="text-sm font-semibold text-slate-100 group-hover:text-white">
          {title}
          <ArrowUpRight className="ml-1 inline h-3.5 w-3.5 text-slate-500 transition group-hover:text-cyan" />
        </div>
        <div className="mt-0.5 text-xs leading-5 text-slate-400">{body}</div>
      </div>
    </button>
  )
}
