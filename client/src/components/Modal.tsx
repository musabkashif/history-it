import { useEffect, useId, useRef, type FormEventHandler, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from './ui'

/** Escape / backdrop / close control. Busy submissions cannot dismiss. */
export function allowModalDismiss(busy: boolean): boolean {
  return !busy
}

type ModalProps = {
  open: boolean
  title: string
  description?: string
  eyebrow?: string
  busy?: boolean
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  /** When set, wraps body+footer in a form. */
  onSubmit?: FormEventHandler<HTMLFormElement>
  size?: 'md' | 'lg'
}

export function Modal({
  open,
  title,
  description,
  eyebrow,
  busy = false,
  onClose,
  children,
  footer,
  onSubmit,
  size = 'md',
}: ModalProps) {
  const titleId = useId()
  const descriptionId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const previouslyFocused = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    previouslyFocused.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const panel = panelRef.current
    const focusable = panel?.querySelector<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )
    focusable?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        if (allowModalDismiss(busy)) onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previouslyFocused.current?.focus?.()
    }
  }, [open, busy, onClose])

  if (!open) return null

  const width = size === 'lg' ? 'max-w-2xl' : 'max-w-lg'
  const body = (
    <>
      <div className="flex items-start justify-between gap-4">
        <div>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h2 id={titleId} className="mt-1 text-lg font-semibold text-white">{title}</h2>
          {description && <p id={descriptionId} className="mt-1 text-xs leading-5 text-slate-400">{description}</p>}
        </div>
        <button
          type="button"
          aria-label="Close"
          className="rounded-lg p-1 text-slate-300 hover:bg-white/5 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => { if (allowModalDismiss(busy)) onClose() }}
          disabled={busy}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="mt-6">{children}</div>
      {footer && <div className="mt-6 flex flex-wrap justify-end gap-2">{footer}</div>}
    </>
  )

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={event => {
        if (event.target === event.currentTarget && allowModalDismiss(busy)) onClose()
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={`surface w-full ${width} p-6`}
        onMouseDown={event => event.stopPropagation()}
      >
        {onSubmit ? <form onSubmit={onSubmit}>{body}</form> : body}
      </div>
    </div>
  )
}

export function ModalActions({
  onCancel,
  busy,
  submitLabel,
  submitTone = 'primary',
  cancelLabel = 'Cancel',
  submitDisabled,
}: {
  onCancel: () => void
  busy?: boolean
  submitLabel: ReactNode
  submitTone?: 'primary' | 'secondary'
  cancelLabel?: string
  submitDisabled?: boolean
}) {
  return (
    <>
      <Button type="button" tone="ghost" onClick={onCancel} disabled={busy}>{cancelLabel}</Button>
      <Button type="submit" tone={submitTone} loading={busy} disabled={submitDisabled}>{submitLabel}</Button>
    </>
  )
}
