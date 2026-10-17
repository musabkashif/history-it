import { useState, type FormEvent } from 'react'
import { Boxes, LockKeyhole } from 'lucide-react'
import { Alert, Button } from '../components/ui'
import { ApiError, api } from '../lib/api'

export function LoginPage({ onAuthenticated }: { onAuthenticated: (username: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const session = await api.login(username.trim(), password)
      if (!session.authenticated) throw new Error('Invalid credentials')
      onAuthenticated(session.username || username.trim())
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 429)) {
        setError(err.status === 429 ? 'Too many login attempts. Try again later.' : 'Invalid credentials')
      } else {
        setError(err instanceof Error ? err.message : 'Invalid credentials')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen bg-canvas text-ink">
      <div className="fixed inset-0 -z-0 overflow-hidden">
        <div className="absolute -left-24 -top-24 h-96 w-96 rounded-full bg-cyan/5 blur-3xl" />
        <div className="absolute bottom-0 right-0 h-96 w-96 rounded-full bg-violet/5 blur-3xl" />
      </div>
      <div className="relative z-10 flex min-h-screen items-center justify-center px-4 py-10">
        <form onSubmit={submit} className="surface w-full max-w-md p-6 sm:p-8">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-cyan text-[#062126]">
              <Boxes className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-bold tracking-wide text-white">HistoryIT</div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Archive workspace</div>
            </div>
          </div>
          <div className="mt-8">
            <div className="eyebrow">Sign in</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-white">Operator access</h1>
            <p className="mt-2 text-sm text-slate-500">Use the shared demo account for this deployment.</p>
          </div>
          {error && (
            <div className="mt-5">
              <Alert kind="danger" title="Sign-in failed">{error}</Alert>
            </div>
          )}
          <label className="mt-6 block">
            <span className="label">Username</span>
            <input
              className="input"
              autoComplete="username"
              value={username}
              onChange={e => setUsername(e.target.value)}
              required
            />
          </label>
          <label className="mt-4 block">
            <span className="label">Password</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
            />
          </label>
          <Button tone="primary" type="submit" loading={loading} className="mt-6 w-full">
            <LockKeyhole className="h-4 w-4" />
            Sign in
          </Button>
        </form>
      </div>
    </div>
  )
}
