import { useEffect, useState } from 'react'
import { FolderOpen } from 'lucide-react'
import { api } from '../lib/api'
import type { ControlWorkspace } from '../lib/types'
import { controlMessage, controlView, shown } from '../lib/controlPlaneState'
import { Alert, Button, EmptyState, SectionHeader } from '../components/ui'

export function ProjectsPage({ onOpenProject }: { onOpenProject: (workspace: ControlWorkspace) => void }) {
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [offline, setOffline] = useState(false)
  const [disabled, setDisabled] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const [rows, setRows] = useState<ControlWorkspace[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [reloadKey, setReloadKey] = useState(0)
  const [projectName, setProjectName] = useState('')
  const [organization, setOrganization] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([api.controlWorkspaces(), api.controlJobs().catch(() => null)]).then(([data, jobs]) => {
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

  return (
    <>
      <SectionHeader eyebrow="Projects" title="Projects" description="Open a project to see its jobs. Google Sheets stays the authoritative register." />
      {view !== 'ready' && view !== 'empty' && (
        <Alert kind={view === 'error' ? 'danger' : 'warning'} title={view === 'loading' ? 'Loading' : 'Projects'}>
          {warning || controlMessage(view)}
        </Alert>
      )}
      {view === 'empty' && <EmptyState title="No projects yet" body="Create a project by name. That does not create a register or start inspection." />}
      {view === 'ready' && (
        <div className="grid gap-4 md:grid-cols-2">
          {rows.map(row => (
            <button key={row.workspace_id} onClick={() => onOpenProject(row)} className="surface p-5 text-left transition hover:border-cyan/40">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-wide text-slate-400">{shown(row.environment)}</div>
                  <h2 className="mt-1 text-lg font-semibold text-white">{shown(row.project_name)}</h2>
                </div>
                <FolderOpen className="h-5 w-5 text-cyan" />
              </div>
              <dl className="mt-4 space-y-1 text-xs text-slate-300">
                <div>Organization: {shown(row.client_organization)}</div>
                <div>Jobs: {counts[row.workspace_id] ?? 0}</div>
              </dl>
            </button>
          ))}
        </div>
      )}
      {view === 'loading' && <div className="mt-4"><Button loading>Loading projects</Button></div>}
      {view !== 'disabled' && view !== 'offline' && view !== 'loading' && (
        <div className="surface mt-6 p-5">
          <h2 className="text-sm font-semibold text-white">Create a project</h2>
          <p className="mt-1 text-xs text-slate-400">Records the project name and an optional organization. It does not create a register, queue a job, or start inspection.</p>
          {createError && <div className="mt-3"><Alert kind="danger" title="Project">{createError}</Alert></div>}
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <input className="input" placeholder="Project name" value={projectName} onChange={event => setProjectName(event.target.value)} disabled={creating} />
            <input className="input" placeholder="Organization, if known" value={organization} onChange={event => setOrganization(event.target.value)} disabled={creating} />
          </div>
          <Button className="mt-4" tone="primary" loading={creating} onClick={createProject}>Create project</Button>
        </div>
      )}
    </>
  )
}
