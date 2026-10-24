import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { ControlJobDetail } from '../lib/types'
import { controlMessage, controlView, jobScreen, resumeAllowed, sectionLabel, shown, sourceLabel } from '../lib/controlPlaneState'
import { Alert, Button, SectionHeader, StatusBadge } from '../components/ui'
import { UsageSummaryPanel } from '../components/UsageSummary'

export function ControlJobPage({
  jobId,
  workspaceId,
  onOpenSheets,
  onBack,
}: {
  jobId: string
  workspaceId?: string
  onOpenSheets: (jobId: string) => Promise<unknown>
  onBack: () => void
}) {
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [offline, setOffline] = useState(false)
  const [disabled, setDisabled] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [detail, setDetail] = useState<ControlJobDetail | null>(null)
  const [action, setAction] = useState('')

  function load() {
    setLoading(true)
    return api.controlJob(jobId, workspaceId).then(data => {
      setDetail(data)
      setDisabled(data.state === 'disabled')
      setOffline(data.state === 'offline')
      setFailed(data.state === 'error')
      setWarning(data.warning || null)
    }).catch(error => {
      setFailed(true)
      setWarning(error instanceof Error ? error.message : 'Could not load job')
      if (error instanceof ApiError && error.status === 503) setOffline(true)
    }).finally(() => setLoading(false))
  }

  useEffect(() => { void load() }, [jobId, workspaceId])

  const view = controlView({ loading, offline, requestFailed: failed && !detail, disabled, empty: false })
  const job = detail?.job
  const readOnly = job ? jobScreen(job.source) === 'record' : false
  const canResume = job ? !readOnly && resumeAllowed(job) : false

  async function reopen() {
    if (!job) return
    setAction('reopen')
    setNotice(null)
    try {
      const next = await api.controlReopen(job.job_id, job.workspace_id)
      setDetail(next)
      if (next.inspection_started) {
        setNotice('Reopen tried to start inspection. That action was not expected.')
        return
      }
      if (job.sheets_reopen_allowed) {
        try {
          await onOpenSheets(job.job_id)
        } catch (error) {
          setNotice(error instanceof Error ? error.message : 'The Sheets job could not be opened. Inspection was not started.')
        }
      } else {
        setNotice('This imported job was reopened here. Inspection was not started.')
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Reopen failed')
    } finally {
      setAction('')
    }
  }

  async function resume() {
    if (!job || !canResume) return
    setAction('resume')
    setNotice(null)
    try {
      await api.resume(job.job_id)
      const next = await api.controlResume(job.job_id, job.workspace_id)
      setDetail(next)
    } catch (error) {
      const sheetsFailed = error instanceof ApiError && error.status !== 503
      if (!sheetsFailed) {
        setNotice('The Sheets resume may have completed, but the control-plane record could not be updated. Google Sheets remains usable.')
      } else {
        setNotice(error instanceof Error ? error.message : 'Resume failed')
      }
    } finally {
      setAction('')
    }
  }

  async function pause() {
    if (!job?.pause_allowed) return
    setAction('pause')
    setNotice(null)
    try {
      await api.pause(job.job_id)
      const next = await api.controlPause(job.job_id, job.workspace_id)
      setDetail(next)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Pause failed')
    } finally {
      setAction('')
    }
  }

  return (
    <>
      <SectionHeader
        eyebrow="Control plane"
        title={shown(job?.project_name)}
        description={job ? `${job.job_id} · ${sourceLabel(job.source)}` : 'Job record'}
        actions={<Button tone="ghost" onClick={onBack}>Back to project</Button>}
      />
      {view !== 'ready' && <Alert kind={view === 'error' ? 'danger' : 'warning'} title="Control plane">{warning || controlMessage(view)}</Alert>}
      {notice && <div className="mb-4"><Alert kind="info" title="Job">{notice}</Alert></div>}
      {job && view === 'ready' && readOnly && (
        <div className="mb-4">
          <Alert kind="info" title="Read only">This historical or imported job can be reviewed here. Continue, pause, and resume stay on a live Sheets job, and inspection is not started from this record.</Alert>
        </div>
      )}
      {job && view === 'ready' && (
        <div className="space-y-6">
          <div className="surface p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-xs text-slate-400">Box {shown(job.box_label)} · counts from {shown(job.summary_origin)}</div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <StatusBadge status={job.job_status || 'unknown'} />
                  <StatusBadge status={sourceLabel(job.source)} />
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {!readOnly && <Button tone="secondary" loading={action === 'reopen'} onClick={reopen}>Reopen</Button>}
                {canResume && <Button tone="primary" loading={action === 'resume'} onClick={resume}>Resume</Button>}
                {!readOnly && job.pause_allowed && <Button tone="ghost" loading={action === 'pause'} onClick={pause}>Pause</Button>}
              </div>
            </div>
            <dl className="mt-5 grid gap-3 text-sm text-slate-300 sm:grid-cols-3">
              <div>Assets: {shown(job.summary_counts?.asset_count)}</div>
              <div>Batches: {shown(job.summary_counts?.batch_count)}</div>
              <div>Release: {shown(job.deployed_release)}</div>
              <div>Register: {job.register_url ? <a className="text-cyan hover:underline" href={job.register_url}>{shown(job.register_id)}</a> : shown(job.register_id || job.register_file_reference)}</div>
              <div>Evidence: {shown(job.completion_evidence)}</div>
              <div>Last error: {shown(job.last_error)}</div>
            </dl>
            <div className="mt-5">
              <UsageSummaryPanel jobId={job.job_id} title="Usage events for this job" />
            </div>
          </div>
          <RecordTable title="Folders" empty={sectionLabel(detail.folders.length, 'folders')} columns={['Folder', 'Order', 'Authorization', 'Processing', 'Assets']} rows={detail.folders.map(folder => [shown(folder.folder_name || folder.folder_id), shown(folder.discovery_order), shown(folder.authorization_status), shown(folder.processing_status), shown(folder.asset_count)])} />
          <RecordTable title="Batches" empty={sectionLabel(detail.batches.length, 'batches')} columns={['Batch', 'Assets', 'Status', 'Validation', 'Commit', 'Attempt', 'Error']} rows={detail.batches.map(batch => [batch.batch_id, shown(batch.asset_count), shown(batch.status), shown(batch.validation_status), shown(batch.commit_status), shown(batch.attempt_count), shown(batch.error_information)])} />
          <RecordTable title="Validation" empty={sectionLabel(detail.validation_results.length, 'validation results')} columns={['Check', 'Scope', 'Result']} rows={detail.validation_results.map(row => [shown(row.check_id), shown(row.scope), shown(row.result)])} />
          <RecordTable title="Checkpoints" empty={sectionLabel(detail.checkpoints.length, 'checkpoints')} columns={['Version', 'State', 'Step', 'Created']} rows={detail.checkpoints.map(row => [shown(row.checkpoint_version), shown(row.current_state), shown(row.last_successful_step), shown(row.created_at)])} />
          <RecordTable title="Events" empty={sectionLabel(detail.events.length, 'events')} columns={['When', 'Type', 'From', 'To', 'Message']} rows={detail.events.map(row => [shown(row.timestamp), shown(row.event_type), shown(row.previous_status), shown(row.new_status), shown(row.message)])} />
          <RecordTable title="Errors and retries" empty={sectionLabel(detail.errors.length + detail.retries.length, 'errors or retries')} columns={['Kind', 'Detail']} rows={[...detail.errors.map(row => ['Error', shown(row.message)]), ...detail.retries.map(row => ['Retry', shown(row.message)])]} />
          {detail.snapshots[0] && (
            <div className="text-xs text-slate-400">Latest register snapshot {shown(detail.snapshots[detail.snapshots.length - 1].captured_at)}. Checksum {shown(detail.snapshots[detail.snapshots.length - 1].readback_checksum)}. Box status {shown(detail.snapshots[detail.snapshots.length - 1].box_status)}.</div>
          )}
        </div>
      )}
    </>
  )
}

function RecordTable({ title, empty, columns, rows }: { title: string; empty: string | null; columns: string[]; rows: string[][] }) {
  return (
    <section className="surface overflow-x-auto p-4">
      <h2 className="mb-3 text-sm font-semibold text-white">{title}</h2>
      {empty ? <p className="text-xs text-slate-400">{empty}</p> : (
        <table className="min-w-full text-left text-xs">
          <thead className="uppercase tracking-wide text-slate-500">
            <tr>{columns.map(column => <th key={column} className="px-2 py-2">{column}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, index) => <tr key={`${title}-${index}`} className="border-t border-white/5">{row.map((cell, cellIndex) => <td key={cellIndex} className="px-2 py-2 text-slate-200">{cell}</td>)}</tr>)}
          </tbody>
        </table>
      )}
    </section>
  )
}
