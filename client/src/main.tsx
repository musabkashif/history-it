import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AppShell, type View } from './components/layout'
import { Alert } from './components/ui'
import { api } from './lib/api'
import { demoBatches, demoCheckpoint, demoCompare, demoJob, demoUsage, demoValidation } from './lib/fixtures'
import type { Batch, CheckpointResponse, CompareResponse, JobForm, JobState, Mode, UsageResponse, ValidationResponse } from './lib/types'
import { Overview } from './pages/Overview'
import { NewJob } from './pages/NewJob'
import { JobPage } from './pages/Job'
import { BenchmarkPage } from './pages/Benchmark'
import { SettingsPage } from './pages/Settings'
import './index.css'

const lastJobKey = 'historyit.lastJobId'
function App() {
  const [view, setView] = useState<View>('overview')
  const [mode, setModeState] = useState<Mode>((localStorage.getItem('historyit.mode') as Mode) || 'preview')
  const [job, setJob] = useState<JobState | null>(mode === 'preview' ? demoJob : null)
  const [batches, setBatches] = useState<Batch[]>(mode === 'preview' ? demoBatches : [])
  const [usage, setUsage] = useState<UsageResponse | null>(mode === 'preview' ? demoUsage : null)
  const [validation, setValidation] = useState<ValidationResponse | null>(mode === 'preview' ? demoValidation : null)
  const [checkpoint, setCheckpoint] = useState<CheckpointResponse | null>(mode === 'preview' ? demoCheckpoint : null)
  const [comparison, setComparison] = useState<CompareResponse | null>(mode === 'preview' ? demoCompare : null)
  const [health, setHealth] = useState<{ status?: string; configured?: boolean } | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [jobError, setJobError] = useState<string | null>(null)
  const [newJobError, setNewJobError] = useState<string | null>(null)

  const setMode = useCallback((next: Mode) => {
    localStorage.setItem('historyit.mode', next); setModeState(next); setError(null); setJobError(null); setNewJobError(null)
    if (next === 'preview') { setJob(demoJob); setBatches(demoBatches); setUsage(demoUsage); setValidation(demoValidation); setCheckpoint(demoCheckpoint); setComparison(demoCompare); setHealth({ status: 'ok', configured: true }) }
    else { setJob(null); setBatches([]); setUsage(null); setValidation(null); setCheckpoint(null); setComparison(null); setHealth(null) }
  }, [])

  const loadJob = useCallback(async (id: string, silent = false) => {
    if (!id) return
    if (!silent) setLoading(true); setJobError(null)
    try { const [nextJob, nextBatches, nextUsage, nextValidation] = await Promise.all([api.getJob(id), api.getBatches(id), api.getUsage(id), api.getValidation(id)]); setJob(nextJob); setBatches(nextBatches.batches || []); setUsage(nextUsage); setValidation(nextValidation); localStorage.setItem(lastJobKey, id); setHealth({ status: 'ok', configured: true }) }
    catch (e) { setJobError(e instanceof Error ? e.message : 'Could not load job') }
    finally { if (!silent) setLoading(false) }
  }, [])
  const refresh = useCallback(async () => { if (mode === 'preview') { setJob(demoJob); setBatches(demoBatches); setUsage(demoUsage); setValidation(demoValidation); return } if (job?.job_id) await loadJob(job.job_id) }, [job?.job_id, loadJob, mode])
  const perform = useCallback(async <T,>(fn: () => Promise<T>, after = true): Promise<T | undefined> => { setActionLoading('action'); setJobError(null); try { const result = await fn(); if (after && mode === 'api' && job?.job_id) await loadJob(job.job_id, true); return result } catch (e) { const message = e instanceof Error ? e.message : 'Action failed'; setJobError(message); setError(message) } finally { setActionLoading('') } return undefined }, [job?.job_id, loadJob, mode])
  useEffect(() => { if (mode !== 'api') return; const id = localStorage.getItem(lastJobKey); if (id && !job) void loadJob(id) }, [loadJob, mode, job])
  useEffect(() => { if (mode !== 'api' || !job?.job_id) return; if (!/Pending|Inspection|Commit|Retryable|Paused/i.test(job.status)) return; const timer = window.setInterval(() => void loadJob(job.job_id, true), 10000); return () => window.clearInterval(timer) }, [job?.job_id, job?.status, loadJob, mode])
  async function createJob(form: JobForm) { setNewJobError(null); if (mode === 'preview') { const next = { ...demoJob, project: form.project, spreadsheet_id: form.spreadsheet_id || demoJob.spreadsheet_id, source_folder_id: form.source_folder_id || demoJob.source_folder_id, job_id: 'HIT-preview-new' }; setJob(next); setBatches(demoBatches); setView('job'); return next } try { const next = await api.createJob(form); setJob(next); localStorage.setItem(lastJobKey, next.job_id); await loadJob(next.job_id, true); setView('job'); return next } catch (e) { const message = e instanceof Error ? e.message : 'Could not create job'; setNewJobError(message); throw e } }
  async function openJob(id: string) { if (mode === 'preview') { setView('job'); return demoJob } await loadJob(id); setView('job'); return job }
  async function healthCheck() { if (mode === 'preview') { setHealth({ status: 'ok', configured: true }); return } try { const h = await api.health(); setHealth(h); setError(null) } catch (e) { const message = e instanceof Error ? e.message : 'Could not reach backend'; setHealth({ status: 'offline' }); setError(message); throw e } }
  async function doAction(fn: () => Promise<unknown>) { if (mode === 'preview') { setActionLoading(''); return demoJob } return perform(fn) }
  const page = view === 'overview' ? <Overview mode={mode} job={job} batches={batches} usage={usage} onNavigate={setView} onRefresh={refresh} loading={loading} /> : view === 'new-job' ? <NewJob onCreated={() => setView('job')} create={createJob} loading={loading} error={newJobError} /> : view === 'job' ? <JobPage job={job} batches={batches} usage={usage} validation={validation} loading={loading} error={jobError} onRefresh={refresh} onStart={() => doAction(() => api.start(job?.job_id || ''))} onPause={() => doAction(() => api.pause(job?.job_id || ''))} onResume={() => doAction(() => api.resume(job?.job_id || ''))} onReconcile={() => doAction(() => api.reconcile(job?.job_id || ''))} onValidate={() => doAction(() => api.validate(job?.job_id || ''))} onRetry={(id) => doAction(() => api.retry(id))} onAuthorize={(folder, size) => doAction(() => api.authorizeFolder(job?.job_id || '', folder, size))} onOpenBenchmark={() => setView('benchmark')} /> : view === 'benchmark' ? <BenchmarkPage loading={actionLoading === 'action' ? 'checkpoint' : ''} error={error} checkpoint={checkpoint} comparison={comparison} onCheckpoint={async id => { if (mode === 'preview') { setCheckpoint(demoCheckpoint); return demoCheckpoint } const r = await perform(() => api.checkpoint(id), false); if (r) setCheckpoint(r); if (!r) throw new Error('Verification did not return a response'); return r }} onCompare={async body => { if (mode === 'preview') { setComparison(demoCompare); return demoCompare } const r = await perform(() => api.compare(body), false); if (r) setComparison(r); if (!r) throw new Error('Comparison did not return a response'); return r }} /> : <SettingsPage mode={mode} setMode={setMode} onHealth={healthCheck} onOpenJob={openJob} currentJobId={job?.job_id || ''} />
  return <AppShell view={view} setView={setView} mode={mode} health={health}>{error && view !== 'benchmark' && <div className="mb-5"><Alert kind="danger" title="Connection message" onClose={() => setError(null)}>{error}</Alert></div>}{page}</AppShell>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
