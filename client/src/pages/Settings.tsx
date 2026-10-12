import { Database, ExternalLink } from 'lucide-react'
import { useRef, useState } from 'react'
import { Alert, Button, SectionHeader } from '../components/ui'

export function SettingsPage({ onOpenJob, currentJobId }: { onOpenJob: (id: string) => Promise<unknown>; currentJobId: string }) {
  const [jobId, setJobId] = useState(currentJobId)
  const [error, setError] = useState('')
  const [opening, setOpening] = useState(false)
  const openingRef = useRef(false)
  async function open() {
    if (!jobId.trim() || openingRef.current) return
    openingRef.current = true
    setOpening(true)
    try { await onOpenJob(jobId.trim()); setError('') }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not open job') }
    finally { openingRef.current = false; setOpening(false) }
  }
  return <>
    <SectionHeader eyebrow="Workspace" title="Open a job" description="Enter the job you want this console to show." />
    <div className="surface max-w-xl p-6 sm:p-8">
      <div className="flex items-center gap-3"><Database className="h-5 w-5 text-violet" /><div><div className="text-sm font-semibold text-white">Existing job</div><div className="text-xs text-slate-500">The workspace loads that job’s current progress.</div></div></div>
      {error && <div className="mt-5"><Alert kind="danger" title="Could not open job">{error}</Alert></div>}
      <input className="input mt-5" value={jobId} onChange={e => setJobId(e.target.value)} placeholder="Job ID" disabled={opening} />
      <Button className="mt-4" tone="primary" onClick={open} loading={opening}><ExternalLink className="h-4 w-4" /> Open job</Button>
    </div>
  </>
}
