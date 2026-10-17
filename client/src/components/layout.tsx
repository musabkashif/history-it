import type { ReactNode } from 'react'
import { useState } from 'react'
import { Activity, ChevronRight, CircleHelp, FileCheck2, LayoutDashboard, LogOut, Menu, PlayCircle, Settings, Sparkles, X } from 'lucide-react'
import { Badge, Button } from './ui'

export type View = 'overview'|'new-job'|'job'|'benchmark'|'settings'

function OdysseyMark({ compact = false }: { compact?: boolean }) {
  // Primary mark matching the logo usage guide (broken ring around O + wordmark).
  const src = '/odyssey-mark-on-dark.svg?v=7'
  return (
    <div className={`flex shrink-0 items-center justify-start overflow-visible ${compact ? 'h-12 w-[13rem]' : 'h-16 w-[14.5rem]'}`}>
      <img
        src={src}
        alt="Odyssey Preservation Software"
        className="h-full w-full object-contain object-left"
        draggable={false}
      />
    </div>
  )
}

export function AppShell({
  view,
  setView,
  health,
  username,
  projectName,
  loadingJob,
  onLogout,
  logoutLoading,
  children,
}: {
  view: View
  setView: (v: View) => void
  health?: { configured?: boolean; status?: string; developer_verification?: boolean } | null
  username?: string
  projectName?: string | null
  loadingJob?: boolean
  onLogout?: () => void
  logoutLoading?: boolean
  children: ReactNode
}) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const developerVerification = Boolean(health?.developer_verification)
  const workspaceLabel = loadingJob
    ? 'Loading workspace…'
    : (projectName && projectName.trim()) || 'Odyssey console'
  const nav = [
    { id: 'overview' as const, label: 'Overview', icon: LayoutDashboard },
    { id: 'new-job' as const, label: 'New workspace', icon: Sparkles },
    { id: 'job' as const, label: 'Current job', icon: PlayCircle },
    ...(developerVerification
      ? [{ id: 'benchmark' as const, label: 'Developer: Box 19 verify', icon: FileCheck2 }]
      : []),
    { id: 'settings' as const, label: 'Settings', icon: Settings },
  ]
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <div className="fixed inset-0 -z-0 overflow-hidden">
        <div className="absolute -left-24 -top-24 h-96 w-96 rounded-full bg-cyan/10 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-96 w-96 rounded-full bg-lime/5 blur-3xl" />
      </div>
      <aside className={`fixed inset-y-0 left-0 z-30 w-64 border-r border-line/50 bg-panel/95 px-4 py-5 backdrop-blur-xl transition-transform lg:translate-x-0 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex items-center justify-between gap-2 overflow-visible px-1 pt-1">
          <OdysseyMark />
          <button className="text-slate-300 lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
        </div>
        <div className="mt-8 px-2 eyebrow">Workspace</div>
        <nav className="mt-3 space-y-1">
          {nav.map(item => {
            const Icon = item.icon
            const active = view === item.id
            return (
              <button
                key={item.id}
                onClick={() => { setView(item.id); setMobileOpen(false) }}
                className={`group flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm transition ${active ? 'bg-cyan/15 text-cyan' : 'text-slate-300 hover:bg-white/5 hover:text-white'}`}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1">{item.label}</span>
                {active && <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            )
          })}
        </nav>
        <div className="mt-10 px-2 eyebrow">System</div>
        <div className="mt-3 rounded-xl border border-line/50 bg-white/[.03] p-3">
          <div className="flex items-center gap-2 text-xs font-medium text-slate-200"><Activity className="h-3.5 w-3.5 text-cyan" /> Connection</div>
          <div className="mt-2 flex items-center justify-between">
            <span className="text-xs text-slate-300">{health?.status === 'ok' ? 'Connected' : health?.status === 'offline' ? 'Offline' : 'Checking'}</span>
            <Badge tone={health?.status === 'ok' ? 'success' : health?.status === 'offline' ? 'danger' : 'neutral'}>{health?.status === 'ok' ? 'On' : health?.status === 'offline' ? 'Off' : '…'}</Badge>
          </div>
          {username && <div className="mt-3 truncate text-[11px] text-slate-300">Signed in as <span className="text-white">{username}</span></div>}
        </div>
        {onLogout && (
          <div className="mt-3 px-0">
            <Button tone="ghost" loading={logoutLoading} className="w-full justify-start px-3" onClick={onLogout}>
              <LogOut className="h-4 w-4" /> Logout
            </Button>
          </div>
        )}
        <div className="absolute bottom-5 left-6 right-6 flex items-center gap-2 text-xs text-slate-400"><CircleHelp className="h-3.5 w-3.5" /> <span>Operator console</span></div>
      </aside>
      <main className="relative z-10 min-h-screen lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-line/50 bg-canvas/80 px-4 backdrop-blur-xl sm:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <button className="rounded-lg p-2 text-slate-300 hover:bg-white/5 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
            <div className="hidden min-w-0 text-xs text-slate-400 sm:block">
              <span className="truncate font-medium text-slate-200">{workspaceLabel}</span>
              <span className="mx-2 text-slate-600">/</span>
              <span>Operator console</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-2 text-xs text-slate-400 sm:flex">
              <span className={`h-1.5 w-1.5 rounded-full ${health?.status === 'ok' ? 'bg-lime' : health?.status === 'offline' ? 'bg-rose' : 'bg-slate-600'}`} />
              {health?.status === 'ok' ? 'Connected' : health?.status === 'offline' ? 'Offline' : 'Connecting'}
            </div>
            {onLogout && (
              <Button tone="ghost" loading={logoutLoading} className="hidden sm:inline-flex" onClick={onLogout}>
                <LogOut className="h-4 w-4" /> Logout
              </Button>
            )}
            <button onClick={() => setView('settings')} className="rounded-lg p-2 text-slate-400 hover:bg-white/5 hover:text-slate-100" aria-label="Settings"><Settings className="h-4 w-4" /></button>
          </div>
        </header>
        <div className="mx-auto max-w-[1480px] px-4 py-7 sm:px-8">{children}</div>
      </main>
    </div>
  )
}
