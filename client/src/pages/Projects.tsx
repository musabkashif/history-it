import { useEffect, useState } from 'react'
import { FolderOpen, Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { ControlWorkspace, UsageSummaryGroup } from '../lib/types'
import { usageSummaryLine } from '../lib/usageSummary'
import { controlMessage, controlView, shown } from '../lib/controlPlaneState'
import { ActivityMeta } from '../components/ActivityMeta'
import { PageLoading } from '../components/loading'
import { Modal, ModalActions } from '../components/Modal'
import { Alert, Button, EmptyState, PageBreadcrumbs, SectionHeader, StatusBadge } from '../components/ui'

export function ProjectsPage({ onOpenProject }: { onOpenProject: (workspace: ControlWorkspace) => void }) {
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [offline, setOffline] = useState(false)
  const [disabled, setDisabled] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [rows, setRows] = useState<ControlWorkspace[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [usageState, setUsageState] = useState('loading')
  const [usageByWorkspace, setUsageByWorkspace] = useState<Record<string, UsageSummaryGroup>>({})
  const [reloadKey, setReloadKey] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [organization, setOrganization] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([api.controlWorkspaces(), api.controlJobs().catch(() => null), api.controlUsageSummary().catch(() => null)]).then(([data, jobs, usage]) => {
      if (cancelled) return
      setRows(data.workspaces || [])
      setDisabled(data.state === 'disabled')
      setOffline(data.state === 'offline')
      setFailed(data.state === 'error')
      setWarning(data.warning || null)
      const next: Record<string, number> = {}
      for (const job of jobs?.jobs || []) {
        next[job.workspace_id] = (next[job.workspace_id] || 0) + 1
      }
      setCounts(next)
      setUsageState(usage?.state || 'error')
      const grouped: Record<string, UsageSummaryGroup> = {}
      for (const item of usage?.by_workspace || []) {
        if (item.workspace_id) grouped[item.workspace_id] = item
      }
      setUsageByWorkspace(grouped)
    }).catch(() => {
      if (cancelled) return
      setFailed(true)
      setOffline(false)
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [reloadKey])

  async function createProject() {
    const name = projectName.trim()
    if (!name || creating) return
    setCreateError('')
    setCreating(true)
    try {
      const created = await api.controlCreateProject(name, organization.trim() || undefined)
      if (created.inspection_started) {
        setCreateError('Creating a project reported that inspection started. That is not allowed.')
        return
      }
      setProjectName('')
      setOrganization('')
      setCreateOpen(false)
      setReloadKey(value => value + 1)
      onOpenProject(created.workspace)
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : 'Could not create project')
    } finally {
      setCreating(false)
    }
  }

  const view = controlView({
    loading,
    offline,
    requestFailed: failed,
    disabled,
    empty: !disabled && !failed && !offline && rows.length === 0,
  })
  const canCreate = view !== 'disabled' && view !== 'offline' && view !== 'loading'
  const createAction = canCreate ? (
    <Button tone="primary" onClick={() => { setCreateError(''); setCreateOpen(true) }}>
      <Plus className="h-4 w-4" /> Create project
    </Button>
  ) : null

  return (
    <>
      <SectionHeader
        breadcrumbs={<PageBreadcrumbs items={[{ label: 'Projects' }]} />}
        eyebrow="Projects"
        title="Projects"
        description="Open a project to see its jobs. Google Sheets stays the authoritative register."
        actions={createAction}
      />
      {view === 'loading' && <PageLoading label="Loading projects" />}
      {view !== 'ready' && view !== 'empty' && view !== 'loading' && (
        <Alert kind={view === 'error' ? 'danger' : 'warning'} title="Projects">
          {warning || controlMessage(view)}
        </Alert>
      )}
      {view === 'empty' && (
        <EmptyState
          title="No projects yet"
          body="Create a project by name. That does not create a register or start inspection."
          action={createAction}
        />
      )}
      {view === 'ready' && (
        <div className="grid gap-4 md:grid-cols-2">
          {rows.map(row => (
            <button key={row.workspace_id} onClick={() => onOpenProject(row)} className="surface p-5 text-left transition hover:border-cyan/40">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={shown(row.status || row.environment || 'workspace')} />
                    <span className="text-xs uppercase tracking-wide text-slate-400">{shown(row.environment)}</span>
                  </div>
                  <h2 className="mt-2 truncate text-lg font-semibold text-white">{shown(row.project_name)}</h2>
                </div>
                <FolderOpen className="h-5 w-5 shrink-0 text-cyan" aria-hidden="true" />
              </div>
              <dl className="mt-4 space-y-1 text-xs text-slate-300">
                <div>Organization: {shown(row.client_organization)}</div>
                <div className="font-medium text-slate-200">Jobs: {counts[row.workspace_id] ?? 0}</div>
                <div>{usageState === 'ready' ? usageSummaryLine(usageByWorkspace[row.workspace_id]) : usageState === 'disabled' ? 'Usage events are off' : 'Usage events unknown'}</div>
              </dl>
              <ActivityMeta className="mt-4" createdAt={row.created_at} updatedAt={row.updated_at} activityLabel="Updated" />
            </button>
          ))}
        </div>
      )}
      <Modal
        open={createOpen}
        title="Create a project"
        eyebrow="Projects"
        description="Records the project name and an optional organization. It does not create a register, queue a job, or start inspection."
        busy={creating}
        onClose={() => { if (!creating) setCreateOpen(false) }}
        onSubmit={event => { event.preventDefault(); void createProject() }}
        footer={(
          <ModalActions
            onCancel={() => { if (!creating) setCreateOpen(false) }}
            busy={creating}
            submitLabel="Create project"
            submitDisabled={!projectName.trim()}
          />
        )}
      >
        {createError && <div className="mb-4"><Alert kind="danger" title="Project">{createError}</Alert></div>}
        <div className="grid gap-4">
          <label>
            <span className="label">Project name</span>
            <input className="input" value={projectName} onChange={event => setProjectName(event.target.value)} disabled={creating} autoFocus required />
          </label>
          <label>
            <span className="label">Organization <span className="font-normal text-slate-400">(optional)</span></span>
            <input className="input" value={organization} onChange={event => setOrganization(event.target.value)} disabled={creating} placeholder="Organization, if known" />
          </label>
        </div>
      </Modal>
    </>
  )
}
