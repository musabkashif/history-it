import { CheckCircle2, ChevronDown, FileCheck2, GitCompareArrows, ShieldAlert, XCircle } from 'lucide-react'
import { useState } from 'react'
import type { CheckpointResponse, CompareResponse } from '../lib/types'
import { Alert, Badge, Button, EmptyState, SectionHeader, StatCard } from '../components/ui'

export function BenchmarkPage({ onCheckpoint, onCompare, loading, error, checkpoint, comparison }: { onCheckpoint: (spreadsheet: string) => Promise<CheckpointResponse>; onCompare: (body: { spreadsheet_id: string; benchmark_spreadsheet_id?: string; box?: string; folder?: string }) => Promise<CompareResponse>; loading?: string; error?: string | null; checkpoint: CheckpointResponse | null; comparison: CompareResponse | null }) {
  const [spreadsheet, setSpreadsheet] = useState(''); const [benchmark, setBenchmark] = useState(''); const [box, setBox] = useState('Chapters Box 19'); const [folder, setFolder] = useState(''); const [active, setActive] = useState<'checkpoint'|'compare'>('checkpoint'); const [running, setRunning] = useState<''|'checkpoint'|'compare'>('')
  async function checkpointRun(e: React.FormEvent) { e.preventDefault(); if (!spreadsheet || running) return; setRunning('checkpoint'); try { await onCheckpoint(spreadsheet) } finally { setRunning('') } }
  async function compareRun(e: React.FormEvent) { e.preventDefault(); if (!spreadsheet || running) return; setRunning('compare'); try { await onCompare({ spreadsheet_id: spreadsheet, benchmark_spreadsheet_id: benchmark || undefined, box, folder }) } finally { setRunning('') } }
  return <><SectionHeader eyebrow="Verification lab" title="Prove the result" description="Run the client-approved F01 checkpoint or the read-only final Frozen Benchmark comparison. These are separate acceptance questions." /><div className="mb-6 grid gap-4 md:grid-cols-2"><button onClick={() => setActive('checkpoint')} disabled={!!running} className={`surface p-5 text-left transition ${active === 'checkpoint' ? 'border-cyan/50 bg-cyan/[.06]' : 'hover:border-line'}`}><div className="flex items-start justify-between"><div className="rounded-xl bg-cyan/10 p-2.5 text-cyan"><FileCheck2 className="h-5 w-5" /></div>{active === 'checkpoint' && <Badge tone="info">Selected</Badge>}</div><h2 className="mt-4 text-base font-semibold text-white">F01 calibration checkpoint</h2><p className="mt-1 text-xs leading-5 text-slate-500">Checks the approved 21-image intermediate state without opening or changing the Frozen Benchmark.</p></button><button onClick={() => setActive('compare')} disabled={!!running} className={`surface p-5 text-left transition ${active === 'compare' ? 'border-violet/50 bg-violet/[.06]' : 'hover:border-line'}`}><div className="flex items-start justify-between"><div className="rounded-xl bg-violet/10 p-2.5 text-violet"><GitCompareArrows className="h-5 w-5" /></div>{active === 'compare' && <Badge tone="info">Selected</Badge>}</div><h2 className="mt-4 text-base font-semibold text-white">Final benchmark comparison</h2><p className="mt-1 text-xs leading-5 text-slate-500">Compares the completed Developer Test register with the read-only Frozen Benchmark, normally after F01–F06.</p></button></div>{error && <div className="mb-5"><Alert kind="danger" title="Verification failed">{error}</Alert></div>}<div className="grid gap-6 xl:grid-cols-[.8fr_1.2fr]"><div className="surface p-6">{active === 'checkpoint' ? <form onSubmit={checkpointRun}><div className="eyebrow">F01 checkpoint</div><h2 className="mt-2 text-lg font-semibold text-white">Confirm the intermediate register</h2><p className="mt-1 text-xs leading-5 text-slate-500">Expected: 21 assets, 2 references, 3 normal records, 2 Ready, 1 Review/Open letter, 0 tasks.</p><label className="mt-6 block"><span className="label">Developer Test spreadsheet ID</span><input className="input" value={spreadsheet} onChange={e => setSpreadsheet(e.target.value)} placeholder="Developer register ID" disabled={!!running} /></label><Button className="mt-5 w-full" tone="primary" type="submit" loading={running === 'checkpoint' || loading === 'checkpoint'} disabled={running === 'compare'}><FileCheck2 className="h-4 w-4" /> Run F01 checkpoint</Button></form> : <form onSubmit={compareRun}><div className="eyebrow">Frozen comparison</div><h2 className="mt-2 text-lg font-semibold text-white">Compare the completed register</h2><p className="mt-1 text-xs leading-5 text-slate-500">Keep the benchmark ID read-only. Leave Folder blank for whole-workbook acceptance.</p><label className="mt-6 block"><span className="label">Developer Test spreadsheet ID</span><input className="input" value={spreadsheet} onChange={e => setSpreadsheet(e.target.value)} placeholder="Developer register ID" disabled={!!running} /></label><label className="mt-4 block"><span className="label">Frozen Benchmark spreadsheet ID <span className="text-slate-600">(optional if configured on backend)</span></span><input className="input" value={benchmark} onChange={e => setBenchmark(e.target.value)} placeholder="Frozen benchmark ID" disabled={!!running} /></label><div className="mt-4 grid gap-4 sm:grid-cols-2"><label><span className="label">Box</span><input className="input" value={box} onChange={e => setBox(e.target.value)} disabled={!!running} /></label><label><span className="label">Folder scope <span className="text-slate-600">(optional)</span></span><input className="input" value={folder} onChange={e => setFolder(e.target.value)} placeholder="F01 or blank" disabled={!!running} /></label></div><Button className="mt-5 w-full" tone="primary" type="submit" loading={running === 'compare' || loading === 'compare'} disabled={running === 'checkpoint'}><GitCompareArrows className="h-4 w-4" /> Run comparison</Button></form>}</div><ResultPanel checkpoint={checkpoint} comparison={comparison} active={active} /></div></>
}
function ResultPanel({ checkpoint, comparison, active }: { checkpoint: CheckpointResponse | null; comparison: CompareResponse | null; active: 'checkpoint'|'compare' }) { const result = active === 'checkpoint' ? checkpoint : comparison; if (!result) return <EmptyState title="No verification run yet" body="Results will appear here with the exact outcome, counts, and mismatch details returned by the backend." />; const compareResult = active === 'compare' ? (result as CompareResponse) : null; const pass = active === 'compare' ? Boolean(compareResult?.archival_matches) : result.matches; const checkpointResult = active === 'checkpoint' ? (result as CheckpointResponse) : null; const counts: Record<string, number> = active === 'checkpoint' ? ((result as CheckpointResponse).counts?.actual || {}) : ((result as CompareResponse).counts?.live || {}); const differences: Record<string, unknown>[] = active === 'compare' ? (((result as CompareResponse).difference_classes || []) as Record<string, unknown>[]) : []; return <div className="space-y-4"><div className={`surface p-6 ${pass ? 'border-emerald-400/30' : 'border-amber/30'}`}><div className="flex items-start gap-4"><div className={`rounded-2xl p-3 ${pass ? 'bg-emerald-400/10 text-emerald-300' : 'bg-amber/10 text-amber'}`}>{pass ? <CheckCircle2 className="h-6 w-6" /> : <ShieldAlert className="h-6 w-6" />}</div><div className="min-w-0"><div className="eyebrow">Backend outcome</div><h2 className="mt-1 break-words text-xl font-semibold text-white">{String((active === 'compare' ? compareResult?.archival_outcome : result.outcome) || result.outcome || 'Unknown')}</h2><p className="mt-2 text-xs leading-5 text-slate-500">{active === 'checkpoint' ? (result.final_box19_match === false ? 'The F01 intermediate state passed. This is intentionally not final Box 19 acceptance.' : '') : result.final_box19_match ? 'The completed register matches the final Box 19 benchmark.' : 'Acceptance uses record groups, template, type, readiness, QC, actions, and tasks. Wording and dates are left out.'}</p></div></div>{active === 'compare' && <div className="mt-5 flex flex-wrap gap-2"><Badge tone={compareResult?.final_box19_match ? 'success' : 'warning'}>Final Box 19: {compareResult?.final_box19_match ? 'PASS' : 'NOT ACCEPTED'}</Badge><Badge tone={compareResult?.archival_matches ? 'success' : 'warning'}>{String(compareResult?.archival_outcome || 'archival comparison')}</Badge><Badge tone={compareResult?.internally_consistent ? 'success' : 'danger'}>Internal consistency: {compareResult?.internally_consistent ? 'PASS' : 'FAIL'}</Badge></div>}</div>{active === 'checkpoint' && <div className="grid gap-4 sm:grid-cols-3">{Object.entries(counts).slice(0, 6).map(([key, value]) => <StatCard key={key} label={key.replaceAll('_', ' ')} value={String(value as number)} tone="cyan" />)}</div>}{active === 'checkpoint' && checkpointResult?.checks && <div className="surface overflow-hidden"><div className="border-b border-line/70 px-5 py-4 text-sm font-semibold text-slate-200">Checkpoint checks</div><div className="grid gap-px bg-line/40 sm:grid-cols-2">{Object.entries(checkpointResult?.checks || {}).map(([key, value]) => <div key={key} className="flex items-center justify-between bg-panel px-5 py-3 text-xs"><span className="text-slate-400">{key.replaceAll('_', ' ')}</span>{Boolean(value) ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <XCircle className="h-4 w-4 text-rose-300" />}</div>)}</div></div>}{active === 'compare' && <CompareDetails result={comparison!} differences={differences} />}</div> }
const MATCH_LABELS: { key: string; label: string }[] = [
  { key: 'files', label: 'Source files' },
  { key: 'files_in_same_group', label: 'Same record' },
  { key: 'file_groups', label: 'Record groups' },
  { key: 'template', label: 'Template' },
  { key: 'type', label: 'Type' },
  { key: 'readiness', label: 'Readiness' },
  { key: 'qc_status', label: 'QC status' },
  { key: 'action', label: 'Action' },
  { key: 'crop_standard', label: 'Crop standard' },
  { key: 'tasks', label: 'Tasks' },
]

const MATTER_LABELS: Record<string, string> = {
  'Digital Assets': 'Record boundaries',
  'QC Status': 'QC status',
  'Action Needed': 'Actions and tasks',
  'Required Crop Standard': 'Crop standards',
  Type: 'Type',
  Template: 'Template',
  'Metadata Readiness': 'Readiness',
  'Box Status': 'Closeout',
}

function shortText(value: unknown) {
  return String(value || '').replaceAll('TriDelta_Year2_Chapters_Box19_', '').replaceAll('.jpg', '')
}

function matterLine(row: Record<string, unknown>) {
  const live = shortText(row.raw_live)
  const benchmark = shortText(row.raw_benchmark)
  if (row.field === 'Digital Assets') return live ? `This run: ${live}` : `Benchmark: ${benchmark}`
  const name = shortText(row.key)
  return `${name} · this run ${live || '—'} · benchmark ${benchmark || '—'}`
}

function CompareDetails({ result }: { result: CompareResponse; differences: Record<string, unknown>[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const match = result.archival_match || {}
  const matter = (result.archival_differences || []).filter(row => row.material && !String(row.reason || '').startsWith('Descriptive prose'))
  const groups = new Map<string, Record<string, unknown>[]>()
  for (const row of matter) {
    const label = MATTER_LABELS[String(row.field)] || String(row.field || 'Difference')
    groups.set(label, [...(groups.get(label) || []), row])
  }
  return <div className="surface overflow-hidden">
    <div className="border-b border-line/70 px-5 py-4">
      <div className="text-sm font-semibold text-slate-200">Match</div>
      <div className="mt-1 text-xs text-slate-600">Percent of benchmark decisions this register reproduces. Wording, dates, and row text are not included.</div>
    </div>
    <div className="grid gap-px bg-line/40 sm:grid-cols-2 xl:grid-cols-5">
      {MATCH_LABELS.map(item => {
        const rate = match[item.key]
        const percent = rate ? `${rate.percent}%` : '—'
        const tone = !rate ? 'text-slate-400' : rate.percent === 100 ? 'text-emerald-300' : rate.percent >= 80 ? 'text-amber' : 'text-rose-300'
        return <div key={item.key} className="bg-panel px-4 py-4">
          <div className={`text-2xl font-semibold ${tone}`}>{percent}</div>
          <div className="mt-1 text-xs text-slate-400">{item.label}</div>
          {rate && <div className="mt-1 text-[11px] text-slate-600">{rate.matched} of {rate.total}</div>}
        </div>
      })}
    </div>
    <div className="border-t border-line/70 px-5 py-4">
      <div className="text-sm font-semibold text-slate-200">Differences that matter</div>
      <div className="mt-1 text-xs text-slate-600">{matter.length === 0 ? 'No material decision differences.' : `${matter.length} decision differences. Open a group to see the files.`}</div>
    </div>
    <div className="divide-y divide-line/50">
      {[...groups.entries()].map(([label, rows]) => <div key={label}>
        <button className="flex w-full items-center justify-between px-5 py-3 text-left hover:bg-white/[.02]" onClick={() => setOpen(open === label ? null : label)}>
          <span className="text-sm text-slate-300">{label}</span>
          <span className="flex items-center gap-2 text-xs text-slate-500">{rows.length}<ChevronDown className={`h-4 w-4 transition ${open === label ? 'rotate-180' : ''}`} /></span>
        </button>
        {open === label && <div className="max-h-72 space-y-1 overflow-auto bg-black/10 px-5 pb-4 text-xs text-slate-400">{rows.map((row, index) => <div key={index}>{matterLine(row)}</div>)}</div>}
      </div>)}
    </div>
  </div>
}
import React from 'react'
