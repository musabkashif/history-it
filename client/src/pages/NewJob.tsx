import { ArrowRight, ClipboardPaste, FolderCog, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useRef, useState } from 'react'
import type { JobForm, JobState } from '../lib/types'
import { Button, SectionHeader, Alert } from '../components/ui'

const initial: JobForm = { project: '', source_folder_id: '', spreadsheet_id: '', batch_size: 10, box: '' }

export function NewJob({ onCreated, create, error }: { onCreated: (job: JobState) => void; create: (form: JobForm) => Promise<JobState>; error?: string | null }) {
  const [form, setForm] = useState<JobForm>(initial)
  const [localError, setLocalError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const set = (key: keyof JobForm, value: string | number) => setForm(v => ({ ...v, [key]: value }))

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setLocalError('')
    if (submittingRef.current) return
    if (!form.project.trim() || !form.source_folder_id.trim() || !form.spreadsheet_id.trim() || !form.box.trim()) {
      setLocalError('Project name, box label, Drive folder link or ID, and Production Register Sheets link or ID are required.')
      return
    }
    submittingRef.current = true
    setSubmitting(true)
    try { onCreated(await create(form)) }
    catch { /* parent surfaces API error */ }
    finally { submittingRef.current = false; setSubmitting(false) }
  }

  return (
    <>
      <SectionHeader
        eyebrow="Workspace setup"
        title="Create a new job"
        description="Authorize one Drive folder or parent box and a clean Developer Test / Production Register. A parent discovers every immediate F-folder child; an individual folder queues only itself. Inspection never starts here."
      />
      <div className="grid gap-6 xl:grid-cols-[1.2fr_.8fr]">
        <form onSubmit={submit} className="surface p-6 sm:p-8">
          <div className="mb-6 flex items-center gap-3">
            <div className="rounded-xl bg-cyan/10 p-3 text-cyan"><FolderCog className="h-5 w-5" /></div>
            <div>
              <h2 className="font-semibold text-white">Job configuration</h2>
              <p className="text-xs text-slate-400">Paste Google links or IDs. Never paste credentials or private keys.</p>
            </div>
          </div>
          {(localError || error) && <div className="mb-5"><Alert kind="danger" title="Could not create job">{localError || error}</Alert></div>}
          <div className="grid gap-5 sm:grid-cols-2">
            <label className="sm:col-span-2">
              <span className="label">Project name</span>
              <input className="input" value={form.project} onChange={e => set('project', e.target.value)} placeholder="Your client / collection name" disabled={submitting} />
            </label>
            <label className="sm:col-span-2">
              <span className="label">Google Drive folder link or ID</span>
              <input className="input" value={form.source_folder_id} onChange={e => set('source_folder_id', e.target.value)} placeholder="Parent box or F-folder link / ID" disabled={submitting} />
            </label>
            <label className="sm:col-span-2">
              <span className="label">Production Register Google Sheets link or ID</span>
              <input className="input" value={form.spreadsheet_id} onChange={e => set('spreadsheet_id', e.target.value)} placeholder="Developer Test or Production Register link / ID" disabled={submitting} />
            </label>
            <label>
              <span className="label">Batch size</span>
              <input className="input" type="number" min={1} max={100} value={form.batch_size} onChange={e => set('batch_size', Number(e.target.value))} disabled={submitting} />
              <span className="mt-1 block text-[11px] text-slate-500">The backend uses this exact bound for each folder.</span>
            </label>
            <label>
              <span className="label">Box label</span>
              <input className="input" value={form.box} onChange={e => set('box', e.target.value)} placeholder="Box name as recorded in the register" disabled={submitting} />
            </label>
          </div>
          <div className="mt-7 flex flex-wrap items-center gap-3">
            <Button tone="primary" type="submit" loading={submitting}>
              <ClipboardPaste className="h-4 w-4" /> Create and queue job <ArrowRight className="h-4 w-4" />
            </Button>
            <div className="flex items-center gap-2 text-xs text-slate-500"><LockKeyhole className="h-3.5 w-3.5" /> No inspection starts here</div>
          </div>
        </form>
        <div className="space-y-4">
          <div className="surface p-6">
            <div className="eyebrow">Safe start</div>
            <h2 className="mt-2 text-lg font-semibold text-white">What this action does</h2>
            <ul className="mt-5 space-y-4 text-sm text-slate-400">
              <li className="flex gap-3"><span className="text-cyan">01</span><span>Checks the register is clean and has no prior production rows.</span></li>
              <li className="flex gap-3"><span className="text-cyan">02</span><span>Enumerates the authorized folder or discovers its immediate F-folder children.</span></li>
              <li className="flex gap-3"><span className="text-cyan">03</span><span>Creates immutable manifests and Batch Queue rows for the frozen scope.</span></li>
              <li className="flex gap-3"><span className="text-cyan">04</span><span>Waits for one Start, which then advances batches and folders on the backend.</span></li>
            </ul>
          </div>
          <div className="surface-muted p-5">
            <div className="flex gap-3">
              <ShieldCheck className="h-5 w-5 shrink-0 text-lime" />
              <div>
                <div className="text-sm font-semibold text-slate-100">Access reminder</div>
                <div className="mt-1 text-xs leading-5 text-slate-400">
                  Share Drive source folders with the service account as <strong className="font-medium text-slate-200">Viewer</strong>.
                  Share the Production Register as <strong className="font-medium text-slate-200">Editor</strong>. See Settings → Operator guide.
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
