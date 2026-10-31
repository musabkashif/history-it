import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AppShell, type View } from './components/layout'
import { Alert } from './components/ui'
import { ApiError, api } from './lib/api'
import type { Batch, CheckpointResponse, CompareResponse, ControlJob, ControlWorkspace, Health, JobForm, JobState, UsageResponse, ValidationResponse } from './lib/types'
import { jobIsActive, displayJobStatus } from './lib/types'
import { databaseWarning, jobScreen } from './lib/controlPlaneState'
import { Overview } from './pages/Overview'
import { NewJob } from './pages/NewJob'
import { JobPage } from './pages/Job'
import { ProjectsPage } from './pages/Projects'
import { JobsPage } from './pages/Jobs'
import { ControlJobPage } from './pages/ControlJob'
import { BenchmarkPage } from './pages/Benchmark'
import { SettingsPage } from './pages/Settings'
import { LoginPage } from './pages/Login'
import './index.css'

function lastJobStorageKey(userId?: string | null) {
  return userId ? `historyit.lastJobId.${userId}` : 'historyit.lastJobId'
}

function App() {
  const [authChecked, setAuthChecked] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [username, setUsername] = useState('')
  const [userId, setUserId] = useState<string | null>(null)
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('admin')
  const [logoutLoading, setLogoutLoading] = useState(false)
  const [view, setView] = useState<View>('overview')
  const [job, setJob] = useState<JobState | null>(null)
  const [batches, setBatches] = useState<Batch[]>([])
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [validation, setValidation] = useState<ValidationResponse | null>(null)
  const [checkpoint, setCheckpoint] = useState<CheckpointResponse | null>(null)
  const [comparison, setComparison] = useState<CompareResponse | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [jobError, setJobError] = useState<string | null>(null)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null)
  const [newJobError, setNewJobError] = useState<string | null>(null)
  const [controlWorkspace, setControlWorkspace] = useState('')
  const [controlProject, setControlProject] = useState('')
  const [controlJobId, setControlJobId] = useState('')
  const [draftProject, setDraftProject] = useState('')
  const [openedFromProject, setOpenedFromProject] = useState(false)

  const jobIdRef = useRef<string | null>(null)
  const loadSeqRef = useRef(0)
  const inFlightRef = useRef(false)

  useEffect(() => { jobIdRef.current = job?.job_id || null }, [job?.job_id])

  const handleUnauthorized = useCallback(() => {
    setAuthenticated(false)
    setUsername('')
    setUserId(null)
    setDisplayName('')
    setRole('admin')
    setJob(null)
    setBatches([])
    setUsage(null)
    setValidation(null)
  }, [])

  const applySession = useCallback((session: { username?: string; user_id?: string | null; display_name?: string; role?: string }) => {
    setUsername(session.username || '')
    setUserId(session.user_id || null)
    setDisplayName(session.display_name || session.username || '')
    setRole(session.role || 'admin')
  }, [])

  const loadJob = useCallback(async (id: string, silent = false) => {
    if (!id) return
    if (inFlightRef.current && silent) return
    const seq = ++loadSeqRef.current
    inFlightRef.current = true
    if (!silent) setLoading(true)
    if (!silent) setJobError(null)
    try {
      const [nextJob, nextBatches, nextUsage, nextValidation] = await Promise.all([
        api.getJob(id), api.getBatches(id), api.getUsage(id), api.getValidation(id),
      ])
      // Ignore stale responses after workspace switch or newer refresh.
      if (seq !== loadSeqRef.current || jobIdRef.current && jobIdRef.current !== id && silent) {
        return
      }
      if (jobIdRef.current && jobIdRef.current !== id) {
        return
      }
      setJob(nextJob)
      setBatches(nextBatches.batches || [])
      setUsage(nextUsage)
      setValidation(nextValidation)
      localStorage.setItem(lastJobStorageKey(userId), id)
      setHealth(current => ({ ...(current || { status: 'ok' }), status: 'ok', configured: true }))
      setRefreshError(null)
      setLastRefreshAt(new Date().toISOString())
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        handleUnauthorized()
        return
      }
      const message = e instanceof Error ? e.message : 'Could not load job'
      // Transient network failure is a refresh problem, not proof the worker stopped.
      if (silent) setRefreshError(message)
      else setJobError(message)
    } finally {
      if (seq === loadSeqRef.current) inFlightRef.current = false
      if (!silent && seq === loadSeqRef.current) setLoading(false)
    }
  }, [handleUnauthorized, userId])

  const refresh = useCallback(async () => { if (job?.job_id) await loadJob(job.job_id) }, [job?.job_id, loadJob])

  const perform = useCallback(async <T,>(fn: () => Promise<T>, after = true, action = 'action'): Promise<T | undefined> => {
    setActionLoading(action)
    setJobError(null)
    try {
      const result = await fn()
      if (after && job?.job_id) await loadJob(job.job_id, true)
      return result
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        handleUnauthorized()
        return undefined
      }
      const message = e instanceof Error ? e.message : 'Action failed'
      setJobError(message)
      setError(message)
    } finally {
      setActionLoading('')
    }
    return undefined
  }, [job?.job_id, loadJob, handleUnauthorized])

  useEffect(() => {
    let cancelled = false
    void api.session().then(session => {
      if (cancelled) return
      setAuthenticated(Boolean(session.authenticated))
      if (session.authenticated) applySession(session)
      setAuthChecked(true)
    }).catch(() => {
      if (cancelled) return
      setAuthenticated(false)
      setAuthChecked(true)
    })
    return () => { cancelled = true }
  }, [applySession])

  useEffect(() => {
    if (!authenticated) return
    const id = localStorage.getItem(lastJobStorageKey(userId))
    if (id) void loadJob(id)
  }, [authenticated, loadJob, userId])

  useEffect(() => {
    if (!authenticated) return
    void api.health().then(h => { setHealth(h); setError(null) }).catch(e => {
      setHealth({ status: 'offline', developer_verification: false })
      setError(e instanceof Error ? e.message : 'Could not reach the archive service')
    })
  }, [authenticated])

  useEffect(() => {
    const base = 'Odyssey · Preservation Software'
    if (!authenticated) {
      document.title = base
      return
    }
    if (loading && !job) {
      document.title = `Loading… · ${base}`
      return
    }
    if (job?.project) {
      document.title = `${job.project} · ${base}`
      return
    }
    document.title = base
  }, [authenticated, job?.project, job?.job_id, loading])

  useEffect(() => {
    if (view === 'benchmark' && !health?.developer_verification) {
      setView('overview')
    }
  }, [view, health?.developer_verification])

  // Poll only while Starting… or the backend worker is live. Idle Authorized jobs do not poll.
  useEffect(() => {
    if (!authenticated || !job?.job_id) return
    const starting = actionLoading === 'start'
    if (!jobIsActive(job, starting)) return
    const id = job.job_id
    const timer = window.setInterval(() => {
      if (jobIdRef.current !== id) return
      void loadJob(id, true)
    }, 4000)
    return () => window.clearInterval(timer)
  }, [authenticated, job, actionLoading, loadJob])

  // Refresh when returning to the job workspace.
  useEffect(() => {
    if (!authenticated || view !== 'job' || !job?.job_id) return
    void loadJob(job.job_id, true)
  }, [authenticated, view, job?.job_id, loadJob])

  async function createJob(form: JobForm) {
    setNewJobError(null)
    try {
      const next = await api.createJob(form)
      jobIdRef.current = next.job_id
      setJob(next)
      localStorage.setItem(lastJobStorageKey(userId), next.job_id)
      await loadJob(next.job_id, true)
      setView('job')
      return next
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        handleUnauthorized()
        throw e
      }
      const message = e instanceof Error ? e.message : 'Could not create job'
      setNewJobError(message)
      throw e
    }
  }

  async function openJob(id: string) {
    jobIdRef.current = id
    await loadJob(id)
    setView('job')
    return job
  }

  async function logout() {
    setLogoutLoading(true)
    try { await api.logout() } catch { /* still clear local auth */ }
    finally {
      setLogoutLoading(false)
      handleUnauthorized()
    }
  }

  if (!authChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-sm text-slate-500">
        Checking session…
      </div>
    )
  }

  if (!authenticated) {
    return (
      <LoginPage
        onAuthenticated={(session) => {
          applySession(session)
          setAuthenticated(true)
        }}
      />
    )
  }

  function navigate(next: View) {
    if (next === 'new-job') setDraftProject('')
    setOpenedFromProject(false)
    setView(next)
  }

  const developerVerification = Boolean(health?.developer_verification)

  const page = view === 'overview'
    ? <Overview job={job} batches={batches} usage={usage} onNavigate={navigate} onRefresh={refresh} loading={loading} lastRefreshAt={lastRefreshAt} refreshError={refreshError} developerVerification={developerVerification} />
    : view === 'new-job'
      ? (
        <NewJob
          key={draftProject || 'blank'}
          projectName={draftProject}
          projectLocked={openedFromProject && Boolean(draftProject.trim())}
          onBackToProject={openedFromProject ? () => setView('jobs') : undefined}
          onCreated={() => setView('job')}
          create={createJob}
          error={newJobError}
        />
      )
      : view === 'job'
        ? <JobPage
            job={job}
            batches={batches}
            usage={usage}
            validation={validation}
            loading={loading}
            error={jobError}
            refreshError={refreshError}
            lastRefreshAt={lastRefreshAt}
            actionLoading={actionLoading}
            developerVerification={developerVerification}
            onRefresh={refresh}
            onStart={() => perform(async () => {
              const started = await api.start(job?.job_id || '')
              if (started) setJob(started)
              return started
            }, true, 'start')}
            onPause={() => perform(() => api.pause(job?.job_id || ''), true, 'pause')}
            onResume={() => perform(() => api.resume(job?.job_id || ''), true, 'resume')}
            onReconcile={() => perform(() => api.reconcile(job?.job_id || ''), true, 'reconcile')}
            onValidate={() => perform(() => api.validate(job?.job_id || ''), true, 'validate')}
            onRetry={(id) => perform(() => api.retry(id), true, id)}
            onAuthorize={(folder, size) => perform(() => api.authorizeFolder(job?.job_id || '', folder, size), true, 'authorize')}
            onOpenBenchmark={() => setView('benchmark')}
            onBackToProject={openedFromProject ? () => setView('jobs') : undefined}
          />
        : view === 'benchmark' && developerVerification
          ? <BenchmarkPage loading={actionLoading} error={error} checkpoint={checkpoint} comparison={comparison} onCheckpoint={async id => { const r = await perform(() => api.checkpoint(id), false, 'checkpoint'); if (r) setCheckpoint(r); if (!r) throw new Error('Verification did not return a response'); return r }} onCompare={async body => { const r = await perform(() => api.compare(body), false, 'compare'); if (r) setComparison(r); if (!r) throw new Error('Comparison did not return a response'); return r }} />
          : view === 'projects'
            ? <ProjectsPage onOpenProject={(workspace: ControlWorkspace) => {
                setControlWorkspace(workspace.workspace_id)
                setControlProject(workspace.project_name || '')
                setView('jobs')
              }} />
            : view === 'jobs'
              ? <JobsPage
                  workspaceId={controlWorkspace}
                  projectName={controlProject}
                  sheetsJob={job ? { job_id: job.job_id, project: job.project, status: displayJobStatus(job) } : null}
                  onOpenJob={(row: ControlJob) => {
                    setControlJobId(row.job_id)
                    setControlWorkspace(row.workspace_id)
                    if (jobScreen(row.source) === 'sheets') {
                      setOpenedFromProject(true)
                      void openJob(row.job_id)
                      return
                    }
                    setView('control-job')
                  }}
                  onOpenSheetsJob={(id) => {
                    setOpenedFromProject(true)
                    void openJob(id)
                  }}
                  onCreateJob={() => {
                    setDraftProject(controlProject)
                    setOpenedFromProject(true)
                    setView('new-job')
                  }}
                  onBack={() => setView('projects')}
                />
              : view === 'control-job'
                ? <ControlJobPage jobId={controlJobId} workspaceId={controlWorkspace} onOpenSheets={openJob} onBack={() => setView('jobs')} />
                : <SettingsPage />

  return (
    <AppShell
      view={view}
      setView={navigate}
      health={health}
      username={username}
      displayName={displayName}
      role={role}
      projectName={controlProject || job?.project}
      jobId={job?.job_id}
      jobStatus={job ? displayJobStatus(job) : null}
      loadingJob={loading && !job}
      onLogout={logout}
      logoutLoading={logoutLoading}
      databaseWarning={databaseWarning(health)}
      inProject={view === 'new-job' && openedFromProject}
    >
      {error && view !== 'benchmark' && (
        <div className="mb-5">
          <Alert kind="danger" title="Connection message" onClose={() => setError(null)}>{error}</Alert>
        </div>
      )}
      {page}
    </AppShell>
  )
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
