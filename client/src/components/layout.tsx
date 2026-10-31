import type { ReactNode } from 'react'
import { useState } from 'react'
import { BookOpen, ChevronRight, FileCheck2, Folders, LayoutDashboard, LogOut, Menu, PlayCircle, Shield, X } from 'lucide-react'
import { Alert, Button } from './ui'

export type View = 'overview'|'new-job'|'job'|'benchmark'|'settings'|'account'|'projects'|'jobs'|'control-job'

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
  displayName,
  role,
  projectName,
  jobId,
  jobStatus,
  loadingJob,
  onLogout,
  logoutLoading,
  databaseWarning,
  inProject = false,
  children,
}: {
  view: View
  setView: (v: View) => void
  health?: { configured?: boolean; status?: string; developer_verification?: boolean } | null
  username?: string
  displayName?: string
  role?: string
  projectName?: string | null
  jobId?: string | null
  jobStatus?: string | null
  loadingJob?: boolean
  onLogout?: () => void
  logoutLoading?: boolean
  databaseWarning?: string | null
  inProject?: boolean
  children: ReactNode
}) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const developerVerification = Boolean(health?.developer_verification)
  const workspaceLabel = loadingJob
    ? 'Loading job…'
    : (projectName && projectName.trim()) || 'Odyssey console'
  const nav = [
    { id: 'overview' as const, label: 'Overview', icon: LayoutDashboard },
    { id: 'projects' as const, label: 'Projects', icon: Folders },
    { id: 'job' as const, label: 'Current job', icon: PlayCircle },
    ...(developerVerification
      ? [{ id: 'benchmark' as const, label: 'Developer: Box 19 verify', icon: FileCheck2 }]
      : []),
    { id: 'account' as const, label: 'Account', icon: Shield },
    { id: 'settings' as const, label: 'Guide', icon: BookOpen },
  ]
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <div className="fixed inset-0 -z-0 overflow-hidden">
        <div className="absolute -left-24 -top-24 h-96 w-96 rounded-full bg-cyan/10 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-96 w-96 rounded-full bg-lime/5 blur-3xl" />
      </div>
      <aside className={`fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-line/50 bg-panel/95 px-4 py-5 backdrop-blur-xl transition-transform lg:translate-x-0 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex items-center justify-between gap-2 overflow-visible px-1 pt-1">
          <OdysseyMark />
          <button className="text-slate-300 lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
        </div>
        <div className="mt-8 px-2 eyebrow">Console</div>
        <nav className="mt-3 space-y-1">
          {nav.map(item => {
            const Icon = item.icon
            const active = view === item.id || (item.id === 'projects' && (inProject || view === 'jobs' || view === 'control-job'))
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
        <div className="mt-auto pt-4">
          {(displayName || username) && (
            <div className="space-y-0.5 px-3 text-[11px] text-slate-300">
              <div className="truncate">Admin <span className="text-white">{displayName || username}</span></div>
              {username && displayName && displayName !== username && (
                <div className="truncate text-slate-500">@{username}</div>
              )}
              {role && <div className="uppercase tracking-wide text-slate-500">{role}</div>}
            </div>
          )}
          {onLogout && (
            <Button tone="ghost" loading={logoutLoading} className="mt-1 w-full justify-start px-3" onClick={onLogout}>
              <LogOut className="h-4 w-4" /> Logout
            </Button>
          )}
        </div>
      </aside>
      <main className="relative z-10 min-h-screen lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-line/50 bg-canvas/80 px-4 backdrop-blur-xl sm:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <button className="rounded-lg p-2 text-slate-300 hover:bg-white/5 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
            <div className="hidden min-w-0 text-xs text-slate-400 sm:block">
              <span className="truncate font-medium text-slate-200">{workspaceLabel}</span>
              <span className="mx-2 text-slate-600">/</span>
              <span>Admin console</span>
              {jobId && (
                <>
                  <span className="mx-2 text-slate-600">/</span>
                  <span className="truncate font-mono text-slate-300" title={jobId}>{jobId.length > 18 ? `${jobId.slice(0, 14)}…` : jobId}</span>
                </>
              )}
              {jobStatus && (
                <>
                  <span className="mx-2 text-slate-600">·</span>
                  <span className="truncate text-slate-300">{jobStatus}</span>
                </>
              )}
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
          </div>
        </header>
        <div className="mx-auto max-w-[1480px] px-4 py-7 sm:px-8">
          {databaseWarning && (
            <div className="mb-5">
              <Alert kind="warning" title="Control database">{databaseWarning}</Alert>
            </div>
          )}
          {children}
        </div>
      </main>
    </div>
  )
}
