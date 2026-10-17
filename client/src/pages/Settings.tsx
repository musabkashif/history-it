import { BookOpen, Database, ExternalLink } from 'lucide-react'
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

  return (
    <>
      <SectionHeader eyebrow="Workspace" title="Settings & guide" description="Reopen a job by ID and review operator access rules. A database workspace browser is not available yet." />
      <div className="grid gap-6 xl:grid-cols-[.9fr_1.1fr]">
        <div className="surface p-6 sm:p-8">
          <div className="flex items-center gap-3">
            <Database className="h-5 w-5 text-cyan" />
            <div>
              <div className="text-sm font-semibold text-white">Existing job</div>
              <div className="text-xs text-slate-400">Paste the Job ID for this console. There is no browseable project list yet.</div>
            </div>
          </div>
          {error && <div className="mt-5"><Alert kind="danger" title="Could not open job">{error}</Alert></div>}
          <input className="input mt-5" value={jobId} onChange={e => setJobId(e.target.value)} placeholder="Job ID" disabled={opening} />
          <Button className="mt-4" tone="primary" onClick={open} loading={opening}><ExternalLink className="h-4 w-4" /> Open job</Button>
        </div>

        <div className="surface p-6 sm:p-8">
          <div className="flex items-center gap-3">
            <BookOpen className="h-5 w-5 text-lime" />
            <div>
              <div className="text-sm font-semibold text-white">Operator guide</div>
              <div className="text-xs text-slate-400">Full detail also lives in <code className="text-slate-300">docs/OPERATOR_GUIDE.md</code>.</div>
            </div>
          </div>
          <div className="mt-5 space-y-4 text-sm leading-6 text-slate-300">
            <p><strong className="text-white">Drive source folders</strong> need the service account as <strong className="text-white">Viewer</strong>. <strong className="text-white">Production Register</strong> spreadsheets need <strong className="text-white">Editor</strong> because Odyssey writes queue and staging rows.</p>
            <p>Find the service-account email in the credentials JSON <code className="text-slate-200">client_email</code> field. Never share the private key.</p>
            <p>Authorize a parent box to discover every immediate <code className="text-slate-200">F*</code> child (images live directly in those F-folders). Or authorize one folder. One <strong className="text-white">Start</strong> advances the whole authorized scope.</p>
            <p>After a backend restart, press <strong className="text-white">Start</strong> again — recovery needs that call; it is not silent auto-resume.</p>
            <p>Paste Drive/Sheets <strong className="text-white">links or IDs</strong>. Crop/QC tasks in the register are recommendations, not proof that pixels were edited. Mechanical validation is not archival judgment QA. Tri Delta QC/readiness issues still need supervision until those separate fixes land.</p>
            <p><strong className="text-white">Developer Tri Delta Box 19 verification</strong> stays hidden unless the server sets <code className="text-slate-200">ENABLE_DEVELOPER_VERIFICATION=true</code>.</p>
          </div>
        </div>
      </div>
    </>
  )
}
