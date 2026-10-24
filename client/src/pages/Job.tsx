import { AlertTriangle, BarChart3, Check, CirclePause, FileCheck2, FolderPlus, Gauge, ListChecks, Play, RefreshCw, RotateCcw, Settings2, ShieldCheck, TerminalSquare, Wrench } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { Batch, JobState, UsageResponse, ValidationResponse } from '../lib/types'
import { displayJobStatus, jobIsRunning, jobIsStartable } from '../lib/types'
import { Button, Badge, EmptyState, SectionHeader, StatCard, StatusBadge, Alert, statusTone } from '../components/ui'
import { UsageSummaryPanel } from '../components/UsageSummary'

export function JobPage({
  job, batches, usage, validation, loading, error, refreshError, lastRefreshAt, actionLoading,
  onRefresh, onStart, onPause, onResume, onReconcile, onValidate, onRetry, onAuthorize, onOpenBenchmark,
  onBackToProject,
  developerVerification = false,
}: {
  job: JobState | null; batches: Batch[]; usage: UsageResponse | null; validation: ValidationResponse | null
  loading?: boolean; error?: string | null; refreshError?: string | null; lastRefreshAt?: string | null; actionLoading?: string
  onRefresh: () => void; onStart: () => Promise<unknown>; onPause: () => Promise<unknown>; onResume: () => Promise<unknown>
  onReconcile: () => Promise<unknown>; onValidate: () => Promise<unknown>; onRetry: (batchId: string) => Promise<unknown>
  onAuthorize: (folder: string, size?: number) => Promise<unknown>; onOpenBenchmark: () => void
  onBackToProject?: () => void
  developerVerification?: boolean
}) {
  const [tab, setTab] = useState<'overview'|'batches'|'evidence'|'usage'>('overview')
  const [authorizeOpen, setAuthorizeOpen] = useState(false)
  const [folder, setFolder] = useState('')
  const [folderSize, setFolderSize] = useState('')
  const [busy, setBusy] = useState('')
  const busyRef = useRef('')
  const committed = batches.filter(b => /Committed - Validated/i.test(String(b['Batch Status']))).length
  const pending = batches.find(b => !/Committed - Validated/i.test(String(b['Batch Status'])))
  const failed = batches.filter(b => /Failed|Blocked/i.test(String(b['Batch Status'])))
  const total = batches.reduce((n, b) => n + Number(b['Planned Asset Count'] || 0), 0)
  const discovered = job?.discovered_folders?.length ? job.discovered_folders : []
  const starting = busy === 'start' || actionLoading === 'start'
  const running = Boolean(job && (jobIsRunning(job) || starting))
  const statusLabel = !job ? '' : displayJobStatus(job, starting)

  useEffect(() => { if (!job) setTab('overview') }, [job])

  async function act(name: string, fn: () => Promise<unknown>) {
    if (busyRef.current) return
    busyRef.current = name
    setBusy(name)
    // Parent perform()/handlers already refresh; avoid a second overlapping loadJob.
    try { await fn() }
    finally { busyRef.current = ''; setBusy('') }
  }

  async function submitFolder(e: React.FormEvent) {
    e.preventDefault()
    if (!folder.trim()) return
    await act('authorize', () => onAuthorize(folder.trim(), folderSize ? Number(folderSize) : undefined))
    setFolder('')
    setFolderSize('')
    setAuthorizeOpen(false)
  }

  if (!job) {
    return (
      <>
        <SectionHeader eyebrow="Odyssey console" title="Current job" description="Open an existing Job ID from Settings, or create a job from a project." />
        <EmptyState title="No job loaded" body="Create a job or open a Job ID. The console stays on this job until another one is opened." />
      </>
    )
  }

  const folderSummary = discovered.length
    ? `${discovered.length} folder${discovered.length === 1 ? '' : 's'}: ${discovered.join(', ')}`
    : 'Authorize a parent box or folder to discover F-folders'

  return (
    <>
      <SectionHeader
        eyebrow="Odyssey console"
        title={job.project || 'Current job'}
        description={`${job.job_id} · register ${job.spreadsheet_id}`}
        actions={(
          <>
            {onBackToProject && <Button tone="ghost" onClick={onBackToProject}>Back to project</Button>}
            <Button tone="ghost" onClick={onRefresh} loading={loading}><RefreshCw className="h-4 w-4" /> Refresh</Button>
          </>
        )}
      />
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <StatusBadge status={statusLabel} />
        {job.alert && <Badge tone="danger"><AlertTriangle className="h-3 w-3" /> Needs attention</Badge>}
        <span className="ml-auto text-xs text-slate-400">
          {lastRefreshAt ? `Last refresh ${new Date(lastRefreshAt).toLocaleTimeString()}` : 'Waiting for refresh'}
          {' · '}Source: {job.source_folder_id}
        </span>
      </div>
      {error && <div className="mb-5"><Alert kind="danger" title="API action failed">{error}</Alert></div>}
      {refreshError && <div className="mb-5"><Alert kind="warning" title="Refresh problem">{refreshError} The backend worker is not assumed stopped.</Alert></div>}
      {job.alert && <div className="mb-5"><Alert kind="warning" title="Backend alert">{job.alert}</Alert></div>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Batches" value={`${committed}/${batches.length}`} hint={pending ? `Next ${pending['Batch ID']}` : job.authorized_scope_complete ? 'Authorized scope complete' : 'All validated'} icon={<ListChecks className="h-5 w-5" />} tone="cyan" />
        <StatCard label="Manifest assets" value={total || '—'} hint="Across authorized folders" icon={<Gauge className="h-5 w-5" />} tone="violet" />
        <StatCard
          label="Discovered folders"
          value={discovered.length || job.authorized_source_folder_ids?.length || 0}
          hint={folderSummary}
          icon={<FolderPlus className="h-5 w-5" />}
          tone="amber"
        />
        <StatCard label="Validation checks" value={validation?.validation?.length || '—'} hint="Readback evidence" icon={<ShieldCheck className="h-5 w-5" />} tone="rose" />
      </div>
      <div className="mt-6 flex flex-wrap items-center gap-1 border-b border-line/70">
        <Tab active={tab === 'overview'} onClick={() => setTab('overview')}>Overview</Tab>
        <Tab active={tab === 'batches'} onClick={() => setTab('batches')}>Batch queue <span className="ml-1 text-slate-400">{batches.length}</span></Tab>
        <Tab active={tab === 'evidence'} onClick={() => setTab('evidence')}>Validation</Tab>
        <Tab active={tab === 'usage'} onClick={() => setTab('usage')}>Usage</Tab>
      </div>
      {tab === 'overview' && (
        <OverviewTab
          job={job}
          pending={pending}
          failed={failed}
          discovered={discovered}
          statusLabel={statusLabel}
          running={running}
          onStart={() => act('start', onStart)}
          onPause={() => act('pause', onPause)}
          onResume={() => act('resume', onResume)}
          onReconcile={() => act('reconcile', onReconcile)}
          onValidate={() => act('validate', onValidate)}
          onAuthorize={() => setAuthorizeOpen(true)}
          onOpenBenchmark={onOpenBenchmark}
          busy={busy || actionLoading || ''}
          developerVerification={developerVerification}
        />
      )}
      {tab === 'batches' && <BatchTable batches={batches} onRetry={(id) => act(id, () => onRetry(id))} busy={busy} />}
      {tab === 'evidence' && <ValidationPanel validation={validation} />}
      {tab === 'usage' && <UsagePanel usage={usage} jobId={job?.job_id} />}
      {authorizeOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <form onSubmit={submitFolder} className="surface w-full max-w-lg p-6">
            <div className="flex items-start justify-between">
              <div>
                <div className="eyebrow">Continuation</div>
                <h2 className="mt-1 text-lg font-semibold text-white">Authorize folder or parent box</h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  A parent discovers remaining immediate F-folders. An individual folder queues only itself. This does not start inspection.
                </p>
              </div>
              <button type="button" onClick={() => { if (busy !== 'authorize') setAuthorizeOpen(false) }} className="text-slate-300" disabled={busy === 'authorize'}>×</button>
            </div>
            <label className="mt-6 block">
              <span className="label">Google Drive folder link or ID</span>
              <input autoFocus className="input" value={folder} onChange={e => setFolder(e.target.value)} placeholder="Parent box or F-folder link / ID" disabled={busy === 'authorize'} />
            </label>
            <label className="mt-4 block">
              <span className="label">Batch size <span className="text-slate-400">(optional)</span></span>
              <input className="input" type="number" min={1} max={100} value={folderSize} onChange={e => setFolderSize(e.target.value)} placeholder="Use configured default" disabled={busy === 'authorize'} />
            </label>
            <div className="mt-6 flex justify-end gap-2">
              <Button type="button" tone="ghost" onClick={() => setAuthorizeOpen(false)} disabled={busy === 'authorize'}>Cancel</Button>
              <Button tone="primary" type="submit" loading={busy === 'authorize'}><FolderPlus className="h-4 w-4" /> Queue folder(s)</Button>
            </div>
          </form>
        </div>
      )}
    </>
  )
}

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className={`border-b-2 px-4 py-3 text-sm font-semibold transition ${active ? 'border-cyan text-cyan' : 'border-transparent text-slate-300 hover:text-white'}`}>{children}</button>
}

function OverviewTab({
  job, pending, failed, discovered, statusLabel, running,
  onStart, onPause, onResume, onReconcile, onValidate, onAuthorize, onOpenBenchmark, busy,
  developerVerification = false,
}: {
  job: JobState; pending?: Batch; failed: Batch[]; discovered: string[]; statusLabel: string; running: boolean
  onStart: () => void; onPause: () => void; onResume: () => void; onReconcile: () => void; onValidate: () => void
  onAuthorize: () => void; onOpenBenchmark: () => void; busy: string
  developerVerification?: boolean
}) {
  const pendingStatus = String(pending?.['Batch Status'] || '')
  const canStart = Boolean(pending) && jobIsStartable(job, pendingStatus) && busy !== 'start'
  const startLabel = statusLabel.startsWith('Starting') || statusLabel.startsWith('Running')
    ? statusLabel
    : 'Start / continue'
  const canResume = Boolean(pending) && !running && (/^Paused$/i.test(pendingStatus) || job.pause_requested) && busy !== 'resume'
  const scopeDone = Boolean(job.authorized_scope_complete)

  return (
    <div className="mt-6 grid gap-6 xl:grid-cols-[1.25fr_.75fr]">
      <div className="surface p-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="eyebrow">Run controls</div>
            <h2 className="mt-2 text-lg font-semibold text-white">{scopeDone ? 'Authorized scope complete' : pending ? 'Continue this workspace' : 'Workspace complete'}</h2>
            <p className="mt-1 text-xs leading-5 text-slate-300">
              One Start advances batches and authorized F-folders on the backend. Folder authorization never starts inspection.
            </p>
            <div className="mt-3 text-xs text-slate-300">
              Folder: {job.current_folder || '—'} · Batch: {job.current_batch || job.next_batch || '—'} · Phase: {job.phase || statusLabel}
            </div>
          </div>
          <Settings2 className="h-5 w-5 text-slate-400" />
        </div>
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {pending || running ? (
            <Button tone="primary" onClick={onStart} loading={busy === 'start'} disabled={!canStart || (!!busy && busy !== 'start')}>
              <Play className="h-4 w-4" /> {startLabel}
            </Button>
          ) : developerVerification ? (
            <Button tone="secondary" onClick={onOpenBenchmark} disabled={!!busy}>
              <FileCheck2 className="h-4 w-4" /> Developer: Tri Delta Box 19 verification
            </Button>
          ) : (
            <Button tone="secondary" onClick={onValidate} loading={busy === 'validate'} disabled={!!busy && busy !== 'validate'}>
              <FileCheck2 className="h-4 w-4" /> Validate readback
            </Button>
          )}
          <Button tone="secondary" onClick={onAuthorize} disabled={!!pending || running || !!busy}>
            <FolderPlus className="h-4 w-4" /> Authorize folder / parent
          </Button>
          <Button tone="ghost" onClick={onPause} loading={busy === 'pause'} disabled={!pending || (!!busy && busy !== 'pause')}>
            <CirclePause className="h-4 w-4" /> Pause job
          </Button>
          <Button tone="ghost" onClick={onResume} loading={busy === 'resume'} disabled={!canResume || (!!busy && busy !== 'resume')}>
            <Play className="h-4 w-4" /> Resume job
          </Button>
          <Button tone="ghost" onClick={onReconcile} loading={busy === 'reconcile'} disabled={!!busy && busy !== 'reconcile'}>
            <Wrench className="h-4 w-4" /> Reconcile register
          </Button>
          <Button tone="ghost" onClick={onValidate} loading={busy === 'validate'} disabled={!!busy && busy !== 'validate'}>
            <Check className="h-4 w-4" /> Validate readback
          </Button>
        </div>
        {failed.length > 0 && (
          <div className="mt-6 rounded-xl border border-rose/20 bg-rose/10 p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-rose-100"><AlertTriangle className="h-4 w-4" /> Retryable attention</div>
            <div className="mt-1 text-xs text-rose-100/70">{failed.length} batch{failed.length > 1 ? 'es' : ''} need review. Open Batch Queue to retry a specific batch.</div>
          </div>
        )}
      </div>
      <div className="surface p-6">
        <div className="eyebrow">Authorized scope</div>
        <h2 className="mt-2 text-lg font-semibold text-white">Discovered folders</h2>
        <div className="mt-5 space-y-3">
          {(discovered.length ? discovered : job.authorized_source_folder_ids).map((id, i) => (
            <div key={`${id}-${i}`} className="flex items-center gap-3">
              <div className="flex h-7 w-7 items-center justify-center rounded-full border border-cyan/30 bg-cyan/10 text-xs font-bold text-cyan">{i + 1}</div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-slate-200">{id}</div>
                <div className="text-[11px] text-slate-400">{discovered.length ? 'Authorized F-folder' : 'Authorized parent / folder ID'}</div>
              </div>
              <Check className="h-4 w-4 text-emerald-400" />
            </div>
          ))}
          {!scopeDone && (
            <div className="flex items-center gap-3">
              <div className="flex h-7 w-7 items-center justify-center rounded-full border border-dashed border-line text-xs text-slate-400">+</div>
              <div className="text-sm text-slate-400">Scope expands only through explicit authorization</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function BatchTable({ batches, onRetry, busy }: { batches: Batch[]; onRetry: (id: string) => void; busy: string }) {
  return (
    <div className="mt-6 surface overflow-hidden">
      <div className="flex items-center justify-between border-b border-line/70 px-5 py-4">
        <div>
          <div className="text-sm font-semibold text-slate-200">Batch Queue</div>
          <div className="text-xs text-slate-400">Immutable manifests, statuses, and continuation evidence</div>
        </div>
        <Badge tone="info">{batches.length} batches</Badge>
      </div>
      {batches.length === 0 ? (
        <div className="p-5"><EmptyState title="No batches yet" body="Create or refresh a job to populate the queue." /></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-xs">
            <thead className="bg-white/[.02] text-[10px] uppercase tracking-wider text-slate-400">
              <tr>
                <th className="px-5 py-3 font-semibold">Batch</th>
                <th className="px-5 py-3 font-semibold">Folder</th>
                <th className="px-5 py-3 font-semibold">Assets</th>
                <th className="px-5 py-3 font-semibold">Status</th>
                <th className="px-5 py-3 font-semibold">Prior open item</th>
                <th className="px-5 py-3 font-semibold">Validation</th>
                <th className="px-5 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {batches.map((b, i) => {
                const status = String(b['Batch Status'] || '')
                const validation = String(b['Validation Status'] || 'Not Run')
                // Pending Validation is not final PASS even if other UI shows success-ish tones.
                const pass = validation.toUpperCase() === 'PASS' && /Committed - Validated/i.test(status)
                const retryable = /Failed|Blocked/i.test(status)
                return (
                  <tr key={String(b['Batch ID'])} className="hover:bg-white/[.02]">
                    <td className="px-5 py-4">
                      <div className="font-semibold text-slate-200">{String(b['Batch ID'])}</div>
                      <div className="mt-1 text-[11px] text-slate-400">Attempt {String(b['Inspection Attempt'] || 0)} · commit {String(b['Commit Attempt'] || 0)}</div>
                    </td>
                    <td className="px-5 py-4">
                      <div className="font-semibold text-slate-300">{String(b['Source Group / Folder'] || `Folder ${i + 1}`)}</div>
                      <div className="mt-1 max-w-[230px] truncate text-[11px] text-slate-400">{String(b['Start Filename'] || 'Manifest available in backend')}</div>
                    </td>
                    <td className="px-5 py-4 font-semibold text-slate-300">{String(b['Planned Asset Count'] || '—')}</td>
                    <td className="px-5 py-4"><StatusBadge status={status} /></td>
                    <td className="max-w-[180px] px-5 py-4"><span className="truncate text-slate-400">{String(b['Prior Open Item'] || 'None')}</span></td>
                    <td className="px-5 py-4">
                      <Badge tone={pass ? 'success' : statusTone(validation)}>
                        {/Commit Written - Pending Validation/i.test(status) ? 'Pending Validation' : validation}
                      </Badge>
                    </td>
                    <td className="px-5 py-4 text-right">
                      {retryable && (
                        <Button tone="danger" onClick={() => onRetry(String(b['Batch ID']))} loading={busy === String(b['Batch ID'])} disabled={!!busy && busy !== String(b['Batch ID'])}>
                          <RotateCcw className="h-3.5 w-3.5" /> Retry
                        </Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ValidationPanel({ validation }: { validation: ValidationResponse | null }) {
  const rows = validation?.validation || []
  return (
    <div className="mt-6 surface overflow-hidden">
      <div className="border-b border-line/70 px-5 py-4">
        <div className="text-sm font-semibold text-slate-200">Validation readback</div>
        <div className="text-xs text-slate-400">Deterministic checks returned by the backend</div>
      </div>
      {rows.length === 0 ? (
        <div className="p-5"><EmptyState title="No validation rows loaded" body="Run Validate readback or refresh after a committed batch." /></div>
      ) : (
        <div className="divide-y divide-line/50">
          {rows.map((row, i) => {
            const result = String(row['Result'] || row['Status'] || '')
            return (
              <div key={i} className="flex items-start gap-3 px-5 py-4">
                <div className={`mt-0.5 rounded-full p-1 ${/pass|success|true/i.test(result) ? 'bg-emerald-400/10 text-emerald-300' : 'bg-amber/10 text-amber'}`}>
                  <Check className="h-3.5 w-3.5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-slate-300">{String(row['Check ID'] || row['Name'] || 'Validation check')}</div>
                  <div className="mt-1 text-xs text-slate-400">{String(row['Detail'] || row['Message'] || row['Notes'] || '')}</div>
                </div>
                <Badge tone={/pass|success|true/i.test(result) ? 'success' : 'warning'}>{result || 'Recorded'}</Badge>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function UsagePanel({ usage, jobId }: { usage: UsageResponse | null; jobId?: string }) {
  const rows = usage?.usage || []
  return (
    <div className="mt-6 space-y-6">
      <UsageSummaryPanel jobId={jobId} title="Usage events for this job" />
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="Register estimate" value={usage?.total_cost_usd != null && usage.total_cost_usd !== '' ? `$${Number(usage.total_cost_usd).toFixed(2)}` : 'Unknown'} hint="Google Sheets OpenAI Usage. Billing reconciliation is pending." icon={<BarChart3 className="h-5 w-5" />} tone="rose" />
        <StatCard label="Inspection calls" value={rows.length || '—'} icon={<TerminalSquare className="h-5 w-5" />} tone="cyan" />
        <StatCard label="Images inspected" value={rows.reduce((n, r) => n + Number(r['Source Image Count'] || 0), 0) || '—'} icon={<Gauge className="h-5 w-5" />} tone="violet" />
      </div>
      <div className="surface overflow-hidden">
        <div className="border-b border-line/70 px-5 py-4 text-sm font-semibold text-slate-200">OpenAI usage ledger</div>
        {rows.length === 0 ? (
          <div className="p-5"><EmptyState title="No usage rows loaded" body="Usage appears after the backend records an inspection attempt." /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] text-left text-xs">
              <thead className="bg-white/[.02] text-[10px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-5 py-3">Call</th>
                  <th className="px-5 py-3">Batch</th>
                  <th className="px-5 py-3">Model</th>
                  <th className="px-5 py-3">Images</th>
                  <th className="px-5 py-3">Tokens</th>
                  <th className="px-5 py-3">Elapsed</th>
                  <th className="px-5 py-3">Cost</th>
                  <th className="px-5 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="px-5 py-4 text-slate-300">{String(r['Call ID'] || '—')}</td>
                    <td className="px-5 py-4 text-slate-300">{String(r['Batch ID'] || '—')}</td>
                    <td className="px-5 py-4 text-slate-300">{String(r.Model || '—')}</td>
                    <td className="px-5 py-4 text-slate-300">{String(r['Source Image Count'] || '—')}</td>
                    <td className="px-5 py-4 text-slate-300">{(Number(r['Input Tokens'] || 0) + Number(r['Output Tokens'] || 0)).toLocaleString()}</td>
                    <td className="px-5 py-4 text-slate-300">{r['Elapsed Seconds'] ? `${r['Elapsed Seconds']}s` : '—'}</td>
                    <td className="px-5 py-4 text-slate-300">{r['Estimated Cost USD'] != null ? `$${Number(r['Estimated Cost USD']).toFixed(2)}` : '—'}</td>
                    <td className="px-5 py-4"><StatusBadge status={String(r.Status || '')} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
