import { Loader2 } from 'lucide-react'

export function PageLoading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="surface-muted flex min-h-40 items-center justify-center gap-3 px-6 py-10 text-sm text-slate-300" role="status" aria-live="polite">
      <Loader2 className="h-4 w-4 animate-spin text-cyan" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function InlineLoading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="inline-flex items-center gap-2 text-xs text-slate-400" role="status" aria-live="polite">
      <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}
