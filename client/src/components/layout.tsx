import type { ReactNode } from 'react'
import { useState } from 'react'
import { Activity, Boxes, ChevronRight, CircleHelp, FileCheck2, LayoutDashboard, LogOut, Menu, PlayCircle, Settings, Sparkles, X } from 'lucide-react'
import { Badge, Button } from './ui'

export type View = 'overview'|'new-job'|'job'|'benchmark'|'settings'
export function AppShell({
  view,
  setView,
  health,
  username,
  onLogout,
  logoutLoading,
  children,
}: {
  view: View
  setView: (v: View) => void
  health?: { configured?: boolean; status?: string } | null
  username?: string
  onLogout?: () => void
  logoutLoading?: boolean
  children: ReactNode
}) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const nav = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'new-job', label: 'New workspace', icon: Sparkles },
    { id: 'job', label: 'Current job', icon: PlayCircle },
    { id: 'benchmark', label: 'Verification', icon: FileCheck2 },
    { id: 'settings', label: 'Settings', icon: Settings },
  ] as const
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <div className="fixed inset-0 -z-0 overflow-hidden">
        <div className="absolute -left-24 -top-24 h-96 w-96 rounded-full bg-cyan/5 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-96 w-96 rounded-full bg-violet/5 blur-3xl" />
      </div>
      <aside className={`fixed inset-y-0 left-0 z-30 w-64 border-r border-line/80 bg-[#091524]/95 px-4 py-5 backdrop-blur-xl transition-transform lg:translate-x-0 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex items-center justify-between px-2">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan text-[#062126]"><Boxes className="h-5 w-5" /></div>
            <div>
              <div className="text-sm font-bold tracking-wide text-white">HistoryIT</div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Archive workspace</div>
            </div>
          </div>
          <button className="text-slate-500 lg:hidden" onClick={() => setMobileOpen(false)}><X className="h-5 w-5" /></button>
        </div>
        <div className="mt-9 px-2 eyebrow">Workspace</div>
        <nav className="mt-3 space-y-1">
          {nav.map(item => {
            const Icon = item.icon
            const active = view === item.id
            return (
              <button
                key={item.id}
                onClick={() => { setView(item.id); setMobileOpen(false) }}
                className={`group flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm transition ${active ? 'bg-cyan/10 text-cyan' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'}`}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1">{item.label}</span>
                {active && <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            )
          })}
        </nav>
        <div className="mt-10 px-2 eyebrow">System</div>
        <div className="mt-3 rounded-xl border border-line/70 bg-white/[.02] p-3">
          <div className="flex items-center gap-2 text-xs font-medium text-slate-300"><Activity className="h-3.5 w-3.5 text-cyan" /> Connection</div>
          <div className="mt-2 flex items-center justify-between">
            <span className="text-xs text-slate-500">{health?.status === 'ok' ? 'Connected' : health?.status === 'offline' ? 'Offline' : 'Checking'}</span>
            <Badge tone={health?.status === 'ok' ? 'success' : health?.status === 'offline' ? 'danger' : 'neutral'}>{health?.status === 'ok' ? 'On' : health?.status === 'offline' ? 'Off' : '…'}</Badge>
          </div>
          {username && <div className="mt-3 truncate text-[11px] text-slate-500">Signed in as <span className="text-slate-300">{username}</span></div>}
        </div>
        {onLogout && (
          <div className="mt-3 px-0">
            <Button tone="ghost" loading={logoutLoading} className="w-full justify-start px-3" onClick={onLogout}>
              <LogOut className="h-4 w-4" /> Logout
            </Button>
          </div>
        )}
        <div className="absolute bottom-5 left-6 right-6 flex items-center gap-2 text-xs text-slate-600"><CircleHelp className="h-3.5 w-3.5" /> <span>Safe operator console</span></div>
      </aside>
      <main className="relative z-10 min-h-screen lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-line/70 bg-canvas/75 px-4 backdrop-blur-xl sm:px-8">
          <div className="flex items-center gap-3">
            <button className="rounded-lg p-2 text-slate-400 hover:bg-white/5 lg:hidden" onClick={() => setMobileOpen(true)}><Menu className="h-5 w-5" /></button>
            <div className="hidden text-xs text-slate-500 sm:block">Tri Delta Chapters <span className="mx-2 text-slate-700">/</span> Operator console</div>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <span className={`h-1.5 w-1.5 rounded-full ${health?.status === 'ok' ? 'bg-emerald-400' : health?.status === 'offline' ? 'bg-rose-400' : 'bg-slate-600'}`} />
              {health?.status === 'ok' ? 'Connected' : health?.status === 'offline' ? 'Offline' : 'Connecting'}
            </div>
            {onLogout && (
              <Button tone="ghost" loading={logoutLoading} className="hidden sm:inline-flex" onClick={onLogout}>
                <LogOut className="h-4 w-4" /> Logout
              </Button>
            )}
            <button onClick={() => setView('settings')} className="rounded-lg p-2 text-slate-500 hover:bg-white/5 hover:text-slate-200"><Settings className="h-4 w-4" /></button>
          </div>
        </header>
        <div className="mx-auto max-w-[1480px] px-4 py-7 sm:px-8">{children}</div>
      </main>
    </div>
  )
}
