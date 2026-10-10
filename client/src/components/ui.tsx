import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Check, ChevronDown, CircleAlert, Info, Loader2, X } from 'lucide-react'

const buttonTones = {
  primary: 'bg-cyan text-[#062126] enabled:hover:bg-[#8af1ed] enabled:active:bg-[#4fd9d4] focus-visible:ring-cyan/50',
  secondary: 'border border-line bg-[#122237] text-slate-200 enabled:hover:border-cyan/40 enabled:hover:bg-[#172a42] enabled:active:bg-[#1c3352] focus-visible:ring-cyan/30',
  ghost: 'text-slate-300 enabled:hover:bg-white/5 enabled:hover:text-white enabled:active:bg-white/10 focus-visible:ring-white/20',
  danger: 'border border-rose/30 bg-rose/10 text-rose-200 enabled:hover:bg-rose/20 enabled:active:bg-rose/25 focus-visible:ring-rose/40',
} as const

export function Button({ tone = 'secondary', loading = false, disabled, className = '', type = 'button', children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: keyof typeof buttonTones; loading?: boolean }) {
  const isDisabled = Boolean(disabled || loading)
  return <button {...props} type={type} disabled={isDisabled} aria-busy={loading || undefined} className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition duration-150 focus-visible:outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-50 ${buttonTones[tone]} ${className}`}>{loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />}{children}</button>
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'success'|'warning'|'danger'|'info'|'neutral' }) {
  const map = { success: 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300', warning: 'border-amber/25 bg-amber/10 text-amber', danger: 'border-rose/25 bg-rose/10 text-rose-200', info: 'border-cyan/20 bg-cyan/10 text-cyan', neutral: 'border-slate-600/50 bg-slate-700/20 text-slate-300' }
  return <span className={`inline-flex max-w-full items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${map[tone]}`}>{children}</span>
}
export function statusTone(status = ''): 'success'|'warning'|'danger'|'info'|'neutral' {
  if (/Committed|PASS|Ready|Success|exact_match|match/i.test(status)) return 'success'
  if (/Pending|Awaiting|Paused|Review|Retryable|mismatch/i.test(status)) return 'warning'
  if (/Failed|Blocked|Error|Unauthorized/i.test(status)) return 'danger'
  if (/Inspection|Running|Processing/i.test(status)) return 'info'
  return 'neutral'
}
export function StatusBadge({ status }: { status?: string }) { return <Badge tone={statusTone(status)}>{status || 'Unknown'}</Badge> }
export function StatCard({ label, value, hint, icon, tone = 'cyan' }: { label: string; value: ReactNode; hint?: string; icon?: ReactNode; tone?: 'cyan'|'amber'|'violet'|'rose' }) {
  const colors = { cyan: 'text-cyan bg-cyan/10', amber: 'text-amber bg-amber/10', violet: 'text-violet bg-violet/10', rose: 'text-rose-200 bg-rose/10' }
  return <div className="surface p-5"><div className="flex items-start justify-between"><div><div className="eyebrow">{label}</div><div className="mt-3 text-2xl font-semibold tracking-tight text-white">{value}</div>{hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}</div>{icon && <div className={`rounded-xl p-2.5 ${colors[tone]}`}>{icon}</div>}</div></div>
}
export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) { return <div className="surface-muted flex min-h-48 flex-col items-center justify-center px-6 text-center"><div className="mb-3 rounded-full border border-line bg-white/5 p-3 text-slate-500"><Info className="h-5 w-5" /></div><h3 className="text-sm font-semibold text-slate-200">{title}</h3><p className="mt-1 max-w-md text-xs leading-5 text-slate-500">{body}</p>{action && <div className="mt-4">{action}</div>}</div> }
export function Alert({ kind = 'info', title, children, onClose }: { kind?: 'info'|'warning'|'danger'|'success'; title?: string; children: ReactNode; onClose?: () => void }) { const C = kind === 'danger' ? CircleAlert : kind === 'success' ? Check : kind === 'warning' ? CircleAlert : Info; const color = kind === 'danger' ? 'border-rose/30 bg-rose/10 text-rose-200' : kind === 'warning' ? 'border-amber/30 bg-amber/10 text-amber' : kind === 'success' ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200' : 'border-cyan/25 bg-cyan/10 text-cyan'; return <div className={`flex gap-3 rounded-xl border p-4 text-sm ${color}`}><C className="mt-0.5 h-4 w-4 shrink-0" /><div className="min-w-0 flex-1"><div className="font-semibold">{title}</div><div className="mt-1 text-xs leading-5 opacity-80">{children}</div></div>{onClose && <button onClick={onClose} className="opacity-60 hover:opacity-100"><X className="h-4 w-4" /></button>}</div> }
export function SectionHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) { return <div className="mb-6 flex flex-wrap items-end justify-between gap-4"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1 className="mt-1 text-2xl font-semibold tracking-tight text-white">{title}</h1>{description && <p className="mt-1 max-w-2xl text-sm text-slate-500">{description}</p>}</div>{actions && <div className="flex items-center gap-2">{actions}</div>}</div> }
export function Select({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) { return <label className="block"><span className="label">{label}</span><div className="relative"><select className="input appearance-none pr-10" value={value} onChange={e => onChange(e.target.value)}>{children}</select><ChevronDown className="pointer-events-none absolute right-3 top-3.5 h-4 w-4 text-slate-500" /></div></label> }
