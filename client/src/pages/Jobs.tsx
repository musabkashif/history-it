import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { ControlJob, ControlJobFilters, UsageSummaryResponse } from '../lib/types'
import { provenCostLabel, usageSummaryLine } from '../lib/usageSummary'
import { controlMessage, controlView, shown, sourceLabel } from '../lib/controlPlaneState'
import { Alert, Button, EmptyState, SectionHeader, StatusBadge } from '../components/ui'

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
      setReloadKey(value => value + 1)
      onOpenJob(created.job)
    } catch (error) {
      setImportError(error instanceof Error ? error.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  return (
    <>
      <SectionHeader
        eyebrow="Project"
        title={projectName || 'Project'}
        description="Jobs for this project only. A live job opens the current Sheets workflow. A historical job stays read-only. Missing usage-event cost stays unknown."
        actions={(
          <>
            <Button tone="ghost" onClick={onBack}>All projects</Button>
            <Button tone="primary" onClick={onCreateJob}>Create a job</Button>
          </>
        )}
      />
      <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <input className="input" placeholder="Box" value={filters.box || ''} onChange={event => setFilters(current => ({ ...current, box: event.target.value }))} />
        <select className="input" value={filters.status || ''} onChange={event => setFilters(current => ({ ...current, status: event.target.value }))}>
          <option value="">Any status</option>
          {['queued', 'running', 'paused', 'blocked', 'validated', 'failed', 'completed', 'unknown'].map(item => <option key={item} value={item}>{item}</option>)}
        </select>
        <select className="input" value={filters.source || ''} onChange={event => setFilters(current => ({ ...current, source: event.target.value }))}>
          <option value="">Any source</option>
          <option value="live">Live</option>
          <option value="imported">Imported</option>
          <option value="historical">Historical</option>
        </select>
        <input className="input" type="date" aria-label="Created from" value={filters.created_from || ''} onChange={event => setFilters(current => ({ ...current, created_from: event.target.value }))} />
        <input className="input" type="date" aria-label="Created to" value={filters.created_to || ''} onChange={event => setFilters(current => ({ ...current, created_to: event.target.value }))} />
      </div>
      {usage?.state === 'ready' && <p className="mb-4 text-sm text-slate-200">{projectName || 'Workspace'} total: {usageSummaryLine(usage.summary)}</p>}
      {usage?.state === 'disabled' && <p className="mb-4 text-xs text-slate-400">Usage events are off. Google Sheets remains the register.</p>}
      {view !== 'ready' && view !== 'empty' && (
        <Alert kind={view === 'error' ? 'danger' : 'warning'} title="Control plane">{warning || controlMessage(view)}</Alert>
      )}
      {view === 'empty' && <EmptyState title="No jobs" body={warning || 'No jobs match these filters.'} />}
      {view === 'ready' && sheetsJobMatches && sheetsJob && (
        <div className="surface mb-4 flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-400">Current Sheets job</div>
            <div className="mt-1 text-sm text-white">{sheetsJob.job_id}</div>
            <div className="mt-1 text-xs text-slate-400">This is the job already open in the Sheets workflow. It is not a second copy.</div>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge status={sheetsJob.status || 'unknown'} />
            <Button tone="primary" onClick={() => onOpenSheetsJob(sheetsJob.job_id)}>Open job</Button>
          </div>
        </div>
      )}
      {view === 'ready' && rows.length > 0 && (
        <div className="surface overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-3">Project</th>
                <th className="px-4 py-3">Box</th>
                <th className="px-4 py-3">Job</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Updated</th>
                <th className="px-4 py-3">Calculated estimate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.job_id} className="border-t border-white/5">
                  <td className="px-4 py-3 text-slate-100">{shown(row.project_name)}</td>
                  <td className="px-4 py-3 text-slate-300">{shown(row.box_label)}</td>
                  <td className="px-4 py-3"><button className="text-cyan hover:underline" onClick={() => onOpenJob(row)}>{row.job_id}</button></td>
                  <td className="px-4 py-3"><StatusBadge status={row.job_status || 'unknown'} /></td>
                  <td className="px-4 py-3 text-slate-300">{sourceLabel(row.source)}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{shown(row.updated_at)}</td>
                  <td className="px-4 py-3 text-slate-200">{usage?.state === 'ready' ? provenCostLabel(usage.by_job.find(item => item.job_id === row.job_id)) : 'Unknown'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="surface mt-6 p-5">
        <h2 className="text-sm font-semibold text-white">Import previous job</h2>
        <p className="mt-1 text-xs text-slate-400">Saves a historical or imported record from a job ID and a register URL or file reference. It does not start inspection or rewrite the register.</p>
        {importError && <div className="mt-3"><Alert kind="danger" title="Import">{importError}</Alert></div>}
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <input className="input" placeholder="Job ID" value={importForm.job_id} onChange={event => setImportForm(current => ({ ...current, job_id: event.target.value }))} />
          <select className="input" value={importForm.source} onChange={event => setImportForm(current => ({ ...current, source: event.target.value as 'historical' | 'imported' }))}>
            <option value="historical">Historical</option>
            <option value="imported">Imported</option>
          </select>
          <input className="input" placeholder="Register URL" value={importForm.register_url} onChange={event => setImportForm(current => ({ ...current, register_url: event.target.value }))} />
          <input className="input" placeholder="Register file reference" value={importForm.register_file_reference} onChange={event => setImportForm(current => ({ ...current, register_file_reference: event.target.value }))} />
          <input className="input" placeholder="Release, if known" value={importForm.deployed_release} onChange={event => setImportForm(current => ({ ...current, deployed_release: event.target.value }))} />
        </div>
        <Button className="mt-4" tone="primary" loading={importing} onClick={submitImport}>Import record</Button>
      </div>
    </>
  )
}
