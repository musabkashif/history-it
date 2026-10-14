import React, { useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AppShell, type View } from './components/layout'
import { Alert } from './components/ui'
import { ApiError, api } from './lib/api'
import type { Batch, CheckpointResponse, CompareResponse, JobForm, JobState, UsageResponse, ValidationResponse } from './lib/types'
import { Overview } from './pages/Overview'
import { NewJob } from './pages/NewJob'
import { JobPage } from './pages/Job'
import { BenchmarkPage } from './pages/Benchmark'
import { SettingsPage } from './pages/Settings'
import { LoginPage } from './pages/Login'
import './index.css'

const lastJobKey = 'historyit.lastJobId'

function App() {
  const [authChecked, setAuthChecked] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [username, setUsername] = useState('')
  const [logoutLoading, setLogoutLoading] = useState(false)
  const [view, setView] = useState<View>('overview')
  const [job, setJob] = useState<JobState | null>(null)
  const [batches, setBatches] = useState<Batch[]>([])
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [validation, setValidation] = useState<ValidationResponse | null>(null)
  const [checkpoint, setCheckpoint] = useState<CheckpointResponse | null>(null)
  const [comparison, setComparison] = useState<CompareResponse | null>(null)
  const [health, setHealth] = useState<{ status?: string; configured?: boolean } | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [jobError, setJobError] = useState<string | null>(null)
  const [newJobError, setNewJobError] = useState<string | null>(null)

  const handleUnauthorized = useCallback(() => {
    setAuthenticated(false)
    setUsername('')
    setJob(null)
    setBatches([])
    setUsage(null)
    setValidation(null)
  }, [])

  const loadJob = useCallback(async (id: string, silent = false) => {
    if (!id) return
    if (!silent) setLoading(true); setJobError(null)
    try {
      const [nextJob, nextBatches, nextUsage, nextValidation] = await Promise.all([
        api.getJob(id), api.getBatches(id), api.getUsage(id), api.getValidation(id),
      ])
      setJob(nextJob)
      setBatches(nextBatches.batches || [])
      setUsage(nextUsage)
      setValidation(nextValidation)
      localStorage.setItem(lastJobKey, id)
      setHealth({ status: 'ok', configured: true })
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        handleUnauthorized()
        return
      }
      setJobError(e instanceof Error ? e.message : 'Could not load job')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [handleUnauthorized])

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
      setUsername(session.username || '')
      setAuthChecked(true)
    }).catch(() => {
      if (cancelled) return
      setAuthenticated(false)
      setAuthChecked(true)
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!authenticated) return
    const id = localStorage.getItem(lastJobKey)
    if (id) void loadJob(id)
  }, [authenticated, loadJob])

  useEffect(() => {
    if (!authenticated) return
    void api.health().then(h => { setHealth(h); setError(null) }).catch(e => {
      setHealth({ status: 'offline' })
      setError(e instanceof Error ? e.message : 'Could not reach the archive service')
    })
  }, [authenticated])

  useEffect(() => {
    if (!authenticated || !job?.job_id) return
    if (!/Pending|Inspection|Commit|Retryable|Paused/i.test(job.status)) return
    const timer = window.setInterval(() => void loadJob(job.job_id, true), 10000)
    return () => window.clearInterval(timer)
  }, [authenticated, job?.job_id, job?.status, loadJob])

  async function createJob(form: JobForm) {
    setNewJobError(null)
    try {
      const next = await api.createJob(form)
      setJob(next)
      localStorage.setItem(lastJobKey, next.job_id)
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
    return <LoginPage onAuthenticated={(name) => { setUsername(name); setAuthenticated(true) }} />
  }

  const page = view === 'overview'
    ? <Overview job={job} batches={batches} usage={usage} onNavigate={setView} onRefresh={refresh} loading={loading} />
    : view === 'new-job'
      ? <NewJob onCreated={() => setView('job')} create={createJob} error={newJobError} />
      : view === 'job'
        ? <JobPage job={job} batches={batches} usage={usage} validation={validation} loading={loading} error={jobError} onRefresh={refresh} onStart={() => perform(() => api.start(job?.job_id || ''))} onPause={() => perform(() => api.pause(job?.job_id || ''))} onResume={() => perform(() => api.resume(job?.job_id || ''))} onReconcile={() => perform(() => api.reconcile(job?.job_id || ''))} onValidate={() => perform(() => api.validate(job?.job_id || ''))} onRetry={(id) => perform(() => api.retry(id))} onAuthorize={(folder, size) => perform(() => api.authorizeFolder(job?.job_id || '', folder, size))} onOpenBenchmark={() => setView('benchmark')} />
        : view === 'benchmark'
          ? <BenchmarkPage loading={actionLoading} error={error} checkpoint={checkpoint} comparison={comparison} onCheckpoint={async id => { const r = await perform(() => api.checkpoint(id), false, 'checkpoint'); if (r) setCheckpoint(r); if (!r) throw new Error('Verification did not return a response'); return r }} onCompare={async body => { const r = await perform(() => api.compare(body), false, 'compare'); if (r) setComparison(r); if (!r) throw new Error('Comparison did not return a response'); return r }} />
          : <SettingsPage onOpenJob={openJob} currentJobId={job?.job_id || ''} />

  return (
    <AppShell view={view} setView={setView} health={health} username={username} onLogout={logout} logoutLoading={logoutLoading}>
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
