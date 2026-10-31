import { useEffect, useState } from 'react'
import { Import, Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { ControlJob, ControlJobFilters, UsageSummaryResponse } from '../lib/types'
import { provenCostLabel, usageSummaryLine } from '../lib/usageSummary'
import { controlMessage, controlView, shown, sourceLabel } from '../lib/controlPlaneState'
import { pickJobActivity } from '../lib/formatTime'
import { ActivityMeta, MetadataLine } from '../components/ActivityMeta'
import { PageLoading } from '../components/loading'
import { Modal, ModalActions } from '../components/Modal'
import { Alert, Button, EmptyState, PageBreadcrumbs, SectionHeader, StatusBadge } from '../components/ui'

export function JobsPage({
  workspaceId,
  projectName,
  sheetsJob,
  onOpenJob,
  onOpenSheetsJob,
  onCreateJob,
  onBack,
}: {
  workspaceId?: string
  projectName?: string | null
  sheetsJob?: { job_id: string; project?: string | null; status?: string | null } | null
  onOpenJob: (job: ControlJob) => void
  onOpenSheetsJob: (jobId: string) => void
  onCreateJob: () => void
  onBack: () => void
}) {
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [offline, setOffline] = useState(false)
  const [disabled, setDisabled] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [rows, setRows] = useState<ControlJob[]>([])
  const [usage, setUsage] = useState<UsageSummaryResponse | null>(null)
  const [filters, setFilters] = useState<ControlJobFilters>({ workspace_id: workspaceId || '' })
  const [importOpen, setImportOpen] = useState(false)
  const [importForm, setImportForm] = useState({ job_id: '', register_url: '', register_file_reference: '', source: 'historical' as 'historical' | 'imported', deployed_release: '' })
  const [importError, setImportError] = useState('')
  const [importing, setImporting] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    setFilters(current => ({ ...current, workspace_id: workspaceId || '' }))
  }, [workspaceId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([
      api.controlJobs(filters),
      api.controlUsageSummary({ workspace_id: workspaceId, project: projectName || undefined }).catch(() => null),
    ]).then(([data, usageSummary]) => {
      if (cancelled) return
      setRows(data.jobs || [])
      setDisabled(data.state === 'disabled')
      setOffline(data.state === 'offline')
      setFailed(data.state === 'error')
      setWarning(data.warning || null)
      setUsage(usageSummary)
    }).catch(() => {
      if (cancelled) return
      setFailed(true)
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [filters, reloadKey, projectName, workspaceId])

  const sheetsJobMatches = Boolean(
    sheetsJob?.job_id
    && projectName
    && (sheetsJob.project || '').trim().toLowerCase() === projectName.trim().toLowerCase()
    && !rows.some(row => row.job_id === sheetsJob.job_id),
  )
  const view = controlView({
    loading,
    offline,
    requestFailed: failed,
    disabled,
    empty: !disabled && !failed && !offline && rows.length === 0 && !sheetsJobMatches,
  })

  async function submitImport() {
    setImportError('')
    setImporting(true)
    try {
      const created = await api.controlImport({
        job_id: importForm.job_id.trim(),
        register_url: importForm.register_url.trim() || undefined,
        register_file_reference: importForm.register_file_reference.trim() || undefined,
        source: importForm.source,
        deployed_release: importForm.deployed_release.trim() || undefined,
      })
      if (created.inspection_started) {
        setImportError('Import reported that inspection started. That is not allowed.')
        return
      }
      setImportOpen(false)
      setReloadKey(value => value + 1)
      onOpenJob(created.job)
    } catch (error) {
      setImportError(error instanceof Error ? error.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const canMutate = view !== 'disabled' && view !== 'offline' && view !== 'loading'
  const createButton = canMutate ? <Button tone="primary" onClick={onCreateJob}><Plus className="h-4 w-4" /> Create a job</Button> : null
  const importButton = canMutate ? (
    <Button tone="secondary" onClick={() => { setImportError(''); setImportOpen(true) }}>
      <Import className="h-4 w-4" /> Import job
    </Button>
  ) : null

  return (
    <>
      <SectionHeader
        breadcrumbs={(
          <PageBreadcrumbs items={[
            { label: 'Projects', onClick: onBack },
            { label: projectName || 'Project' },
          ]} />
        )}
        title={projectName || 'Project'}
        description="Jobs for this project. Live jobs open the Sheets workflow; historical jobs stay read-only."
        actions={(
          <>
            {importButton}
            {createButton}
          </>
        )}
      />
      <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <label>
          <span className="label">Box</span>
          <input className="input" placeholder="Box" value={filters.box || ''} onChange={event => setFilters(current => ({ ...current, box: event.target.value }))} />
        </label>
        <label>
          <span className="label">Status</span>
          <select className="input" value={filters.status || ''} onChange={event => setFilters(current => ({ ...current, status: event.target.value }))}>
            <option value="">Any status</option>
            {['queued', 'running', 'paused', 'blocked', 'validated', 'failed', 'completed', 'unknown'].map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>
          <span className="label">Source</span>
          <select className="input" value={filters.source || ''} onChange={event => setFilters(current => ({ ...current, source: event.target.value }))}>
            <option value="">Any source</option>
            <option value="live">Live</option>
            <option value="imported">Imported</option>
            <option value="historical">Historical</option>
          </select>
        </label>
        <label>
          <span className="label">Created from</span>
          <input className="input" type="date" aria-label="Created from" value={filters.created_from || ''} onChange={event => setFilters(current => ({ ...current, created_from: event.target.value }))} />
        </label>
        <label>
          <span className="label">Created to</span>
          <input className="input" type="date" aria-label="Created to" value={filters.created_to || ''} onChange={event => setFilters(current => ({ ...current, created_to: event.target.value }))} />
        </label>
      </div>
      {usage?.state === 'ready' && <p className="mb-4 text-sm text-slate-200">Total: {usageSummaryLine(usage.summary)}</p>}
      {usage?.state === 'disabled' && <p className="mb-4 text-xs text-slate-400">Usage events are off. Google Sheets remains the register.</p>}
      {view === 'loading' && <PageLoading label="Loading jobs" />}
      {view !== 'ready' && view !== 'empty' && view !== 'loading' && (
        <Alert kind={view === 'error' ? 'danger' : 'warning'} title="Control plane">{warning || controlMessage(view)}</Alert>
      )}
      {view === 'empty' && (
        <EmptyState
          title="No jobs"
          body={warning || 'No jobs match these filters.'}
          action={(
            <div className="flex flex-wrap justify-center gap-2">
              {importButton}
              {createButton}
            </div>
          )}
        />
      )}
      {view === 'ready' && sheetsJobMatches && sheetsJob && (
        <div className="surface mb-4 flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-400">Current Sheets job</div>
            <div className="mt-1 text-sm text-white" title={sheetsJob.job_id}>{sheetsJob.job_id}</div>
            <div className="mt-1 text-xs text-slate-400">This is the job already open in the Sheets workflow. It is not a second copy.</div>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge status={sheetsJob.status || 'unknown'} />
            <Button tone="primary" onClick={() => onOpenSheetsJob(sheetsJob.job_id)}>Open job</Button>
          </div>
        </div>
      )}
      {view === 'ready' && rows.length > 0 && (
        <div className="grid gap-4">
          {rows.map(row => {
            const activity = pickJobActivity(row)
            const progress = row.summary_counts
              ? `${shown(row.summary_counts.completed_batch_count)}/${shown(row.summary_counts.batch_count)} batches`
              : null
            return (
              <button
                key={row.job_id}
                type="button"
                onClick={() => onOpenJob(row)}
                className="surface w-full p-5 text-left transition hover:border-cyan/40"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={row.job_status || 'unknown'} />
                      <span className="text-xs text-slate-400">{sourceLabel(row.source)}</span>
                    </div>
                    <h2 className="mt-2 text-base font-semibold text-white" title={row.job_id}>
                      {shown(row.box_label) !== '—' ? `Box ${shown(row.box_label)}` : 'Job'}
                    </h2>
                    <MetadataLine className="mt-1">
                      <span title={row.job_id}>ID {row.job_id.length > 18 ? `${row.job_id.slice(0, 14)}…` : row.job_id}</span>
                      {progress && <span>Progress {progress}</span>}
                      <span>{usage?.state === 'ready' ? provenCostLabel(usage.by_job.find(item => item.job_id === row.job_id)) : 'Unknown estimate'}</span>
                    </MetadataLine>
                  </div>
                </div>
                <ActivityMeta
                  className="mt-4"
                  createdAt={activity.created}
                  activityAt={activity.activity}
                  activityLabel={activity.activityLabel}
                />
              </button>
            )
          })}
        </div>
      )}
      <Modal
        open={importOpen}
        title="Import previous job"
        eyebrow="Project"
        description="Saves a historical or imported record from a job ID and a register URL or file reference. It does not start inspection or rewrite the register."
        busy={importing}
        size="lg"
        onClose={() => { if (!importing) setImportOpen(false) }}
        onSubmit={event => { event.preventDefault(); void submitImport() }}
        footer={(
          <ModalActions
            onCancel={() => { if (!importing) setImportOpen(false) }}
            busy={importing}
            submitLabel="Import record"
            submitDisabled={!importForm.job_id.trim()}
          />
        )}
      >
        {importError && <div className="mb-4"><Alert kind="danger" title="Import">{importError}</Alert></div>}
        <div className="grid gap-4 md:grid-cols-2">
          <label className="md:col-span-1">
            <span className="label">Job ID</span>
            <input className="input" value={importForm.job_id} onChange={event => setImportForm(current => ({ ...current, job_id: event.target.value }))} disabled={importing} required autoFocus />
          </label>
          <label>
            <span className="label">Source</span>
            <select className="input" value={importForm.source} onChange={event => setImportForm(current => ({ ...current, source: event.target.value as 'historical' | 'imported' }))} disabled={importing}>
              <option value="historical">Historical</option>
              <option value="imported">Imported</option>
            </select>
          </label>
          <label className="md:col-span-2">
            <span className="label">Register URL <span className="font-normal text-slate-400">(optional)</span></span>
            <input className="input" value={importForm.register_url} onChange={event => setImportForm(current => ({ ...current, register_url: event.target.value }))} disabled={importing} />
          </label>
          <label className="md:col-span-2">
            <span className="label">Register file reference <span className="font-normal text-slate-400">(optional)</span></span>
            <input className="input" value={importForm.register_file_reference} onChange={event => setImportForm(current => ({ ...current, register_file_reference: event.target.value }))} disabled={importing} />
          </label>
          <label className="md:col-span-2">
            <span className="label">Release <span className="font-normal text-slate-400">(optional)</span></span>
            <input className="input" value={importForm.deployed_release} onChange={event => setImportForm(current => ({ ...current, deployed_release: event.target.value }))} disabled={importing} placeholder="Release, if known" />
          </label>
        </div>
      </Modal>
    </>
  )
}
