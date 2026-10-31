import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { KeyRound, Shield, UserPlus, Users } from 'lucide-react'
import { Modal } from '../components/Modal'
import { PageLoading } from '../components/loading'
import { Alert, Button, PageBreadcrumbs, SectionHeader, StatusBadge } from '../components/ui'
import { ApiError, api, type AccountResponse, type AdminRecord, type MembershipRecord } from '../lib/api'
import type { ControlWorkspace } from '../lib/types'
import { formatExactTimestamp } from '../lib/formatTime'

type Notice = { kind: 'success' | 'danger' | 'warning' | 'info'; title: string; body: string }

function errorMessage(err: unknown, fallback: string) {
  if (err instanceof ApiError) return err.message || fallback
  if (err instanceof Error && err.message) return err.message
  return fallback
}

function passwordRules(value: string): string | null {
  if (!value) return 'Enter a password.'
  if (value.length < 10) return 'Password must be at least 10 characters.'
  if (value.length > 200) return 'Password is too long.'
  return null
}

export function AccountPage() {
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [account, setAccount] = useState<AccountResponse | null>(null)
  const [admins, setAdmins] = useState<AdminRecord[]>([])
  const [workspaces, setWorkspaces] = useState<ControlWorkspace[]>([])
  const [memberships, setMemberships] = useState<MembershipRecord[]>([])

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordError, setPasswordError] = useState<string | null>(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [createUsername, setCreateUsername] = useState('')
  const [createDisplayName, setCreateDisplayName] = useState('')
  const [createPassword, setCreatePassword] = useState('')
  const [createConfirm, setCreateConfirm] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  const [resetTarget, setResetTarget] = useState<AdminRecord | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetConfirm, setResetConfirm] = useState('')
  const [resetError, setResetError] = useState<string | null>(null)

  const [grantWorkspaceId, setGrantWorkspaceId] = useState('')
  const [grantUserId, setGrantUserId] = useState('')

  const activeAdmins = useMemo(() => admins.filter(row => row.active), [admins])
  const membersByWorkspace = useMemo(() => {
    const map = new Map<string, MembershipRecord[]>()
    for (const row of memberships) {
      const list = map.get(row.workspace_id) || []
      list.push(row)
      map.set(row.workspace_id, list)
    }
    return map
  }, [memberships])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [nextAccount, adminList, workspaceList, memberList] = await Promise.all([
        api.account(),
        api.listAdmins(),
        api.controlWorkspaces(),
        api.listMemberships(),
      ])
      setAccount(nextAccount)
      setAdmins(adminList.admins || [])
      const nextWorkspaces = workspaceList.workspaces || []
      setWorkspaces(nextWorkspaces)
      setMemberships(memberList.memberships || [])
      setGrantWorkspaceId(current => current || nextWorkspaces[0]?.workspace_id || '')
    } catch (err) {
      setNotice({ kind: 'danger', title: 'Could not load account settings', body: errorMessage(err, 'Request failed.') })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  async function onChangePassword(event: FormEvent) {
    event.preventDefault()
    setPasswordError(null)
    const rule = passwordRules(newPassword)
    if (rule) { setPasswordError(rule); return }
    if (newPassword !== confirmPassword) { setPasswordError('New password and confirmation do not match.'); return }
    setBusy('password')
    try {
      await api.changePassword(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setNotice({ kind: 'success', title: 'Password updated', body: 'Your Admin password was changed.' })
      await refresh()
    } catch (err) {
      setPasswordError(errorMessage(err, 'Could not change password.'))
    } finally {
      setBusy('')
    }
  }

  async function onCreateAdmin(event: FormEvent) {
    event.preventDefault()
    setCreateError(null)
    const rule = passwordRules(createPassword)
    if (rule) { setCreateError(rule); return }
    if (createPassword !== createConfirm) { setCreateError('Password and confirmation do not match.'); return }
    setBusy('create')
    try {
      await api.createAdmin({
        username: createUsername.trim(),
        password: createPassword,
        display_name: createDisplayName.trim() || undefined,
      })
      setCreateOpen(false)
      setCreateUsername('')
      setCreateDisplayName('')
      setCreatePassword('')
      setCreateConfirm('')
      setNotice({ kind: 'success', title: 'Admin created', body: 'The new Admin can sign in with the password you set.' })
      await refresh()
    } catch (err) {
      setCreateError(errorMessage(err, 'Could not create Admin.'))
    } finally {
      setBusy('')
    }
  }

  async function onToggleActive(row: AdminRecord) {
    const next = !row.active
    if (!next && activeAdmins.length <= 1) {
      setNotice({ kind: 'warning', title: 'Last active Admin', body: 'Cannot disable the last active Admin.' })
      return
    }
    if (!next && account?.user_id === row.user_id) {
      setNotice({ kind: 'warning', title: 'Own account', body: 'You cannot disable your own Admin account while signed in.' })
      return
    }
    setBusy(`active:${row.user_id}`)
    try {
      await api.setAdminActive(row.user_id, next)
      setNotice({
        kind: 'success',
        title: next ? 'Admin enabled' : 'Admin disabled',
        body: `${row.display_name || row.username} is now ${next ? 'active' : 'disabled'}.`,
      })
      await refresh()
    } catch (err) {
      setNotice({ kind: 'danger', title: 'Could not update Admin', body: errorMessage(err, 'Request failed.') })
    } finally {
      setBusy('')
    }
  }

  async function onResetPassword(event: FormEvent) {
    event.preventDefault()
    if (!resetTarget) return
    setResetError(null)
    const rule = passwordRules(resetPassword)
    if (rule) { setResetError(rule); return }
    if (resetPassword !== resetConfirm) { setResetError('Password and confirmation do not match.'); return }
    setBusy('reset')
    try {
      await api.resetAdminPassword(resetTarget.user_id, resetPassword)
      setResetTarget(null)
      setResetPassword('')
      setResetConfirm('')
      setNotice({ kind: 'success', title: 'Password reset', body: `Password reset for ${resetTarget.username}.` })
      await refresh()
    } catch (err) {
      setResetError(errorMessage(err, 'Could not reset password.'))
    } finally {
      setBusy('')
    }
  }

  async function onGrantMembership(event: FormEvent) {
    event.preventDefault()
    if (!grantWorkspaceId || !grantUserId) {
      setNotice({ kind: 'warning', title: 'Membership', body: 'Choose a workspace and an Admin.' })
      return
    }
    setBusy('grant')
    try {
      await api.grantMembership(grantUserId, grantWorkspaceId)
      setNotice({ kind: 'success', title: 'Access granted', body: 'The Admin can now open this workspace.' })
      await refresh()
    } catch (err) {
      setNotice({ kind: 'danger', title: 'Could not grant access', body: errorMessage(err, 'Request failed.') })
    } finally {
      setBusy('')
    }
  }

  async function onRevokeMembership(row: MembershipRecord) {
    const peers = membersByWorkspace.get(row.workspace_id) || []
    if (peers.length <= 1) {
      setNotice({ kind: 'warning', title: 'Last workspace Admin', body: 'Cannot remove the last Admin from this workspace.' })
      return
    }
    setBusy(`revoke:${row.user_id}:${row.workspace_id}`)
    try {
      await api.revokeMembership(row.user_id, row.workspace_id)
      setNotice({ kind: 'success', title: 'Access removed', body: 'Workspace membership was revoked.' })
      await refresh()
    } catch (err) {
      setNotice({ kind: 'danger', title: 'Could not remove access', body: errorMessage(err, 'Request failed.') })
    } finally {
      setBusy('')
    }
  }

  if (loading && !account) {
    return (
      <>
        <SectionHeader
          breadcrumbs={<PageBreadcrumbs items={[{ label: 'Account' }]} />}
          title="Account"
          description="Manage your Admin identity, directory, and workspace membership."
        />
        <PageLoading label="Loading account settings" />
      </>
    )
  }

  return (
    <>
      <SectionHeader
        breadcrumbs={<PageBreadcrumbs items={[{ label: 'Account' }]} />}
        title="Account"
        description="Admin identity, password, directory, and project membership. Passwords are never shown after you type them."
        actions={(
          <Button tone="primary" onClick={() => { setCreateError(null); setCreateOpen(true) }}>
            <UserPlus className="h-4 w-4" /> New Admin
          </Button>
        )}
      />

      {notice && (
        <div className="mb-5">
          <Alert kind={notice.kind} title={notice.title} onClose={() => setNotice(null)}>{notice.body}</Alert>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <section className="surface p-6">
          <div className="flex items-center gap-3">
            <Shield className="h-5 w-5 text-cyan" />
            <div>
              <div className="text-sm font-semibold text-white">Current account</div>
              <div className="text-xs text-slate-400">Signed-in Admin session</div>
            </div>
          </div>
          <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-xs text-slate-500">Display name</dt><dd className="mt-1 text-slate-100">{account?.display_name || '—'}</dd></div>
            <div><dt className="text-xs text-slate-500">Username</dt><dd className="mt-1 font-mono text-slate-100">{account?.username || '—'}</dd></div>
            <div><dt className="text-xs text-slate-500">Role</dt><dd className="mt-1 uppercase tracking-wide text-slate-100">{account?.role || 'admin'}</dd></div>
            <div><dt className="text-xs text-slate-500">Status</dt><dd className="mt-1"><StatusBadge status={account?.active ? 'Active' : 'Disabled'} /></dd></div>
            <div><dt className="text-xs text-slate-500">User ID</dt><dd className="mt-1 break-all font-mono text-xs text-slate-300">{account?.user_id || '—'}</dd></div>
            <div><dt className="text-xs text-slate-500">Created</dt><dd className="mt-1 text-slate-100">{account?.created_at ? formatExactTimestamp(account.created_at) : '—'}</dd></div>
            <div className="sm:col-span-2"><dt className="text-xs text-slate-500">Session</dt><dd className="mt-1 text-slate-300">{account?.session?.display_name || account?.session?.username || '—'} · {account?.session?.role || 'admin'}</dd></div>
          </dl>
        </section>

        <section className="surface p-6">
          <div className="flex items-center gap-3">
            <KeyRound className="h-5 w-5 text-lime" />
            <div>
              <div className="text-sm font-semibold text-white">Change password</div>
              <div className="text-xs text-slate-400">Requires your current password</div>
            </div>
          </div>
          <form className="mt-5 space-y-3" onSubmit={onChangePassword} autoComplete="off">
            <label className="block">
              <span className="label">Current password</span>
              <input className="input" type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} autoComplete="current-password" required />
            </label>
            <label className="block">
              <span className="label">New password</span>
              <input className="input" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} autoComplete="new-password" required />
            </label>
            <label className="block">
              <span className="label">Confirm new password</span>
              <input className="input" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} autoComplete="new-password" required />
            </label>
            {passwordError && <Alert kind="danger" title="Password not updated">{passwordError}</Alert>}
            <Button type="submit" tone="primary" loading={busy === 'password'}>Update password</Button>
          </form>
        </section>
      </div>

      <section className="surface mt-5 overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line/40 px-6 py-4">
          <Users className="h-5 w-5 text-cyan" />
          <div>
            <div className="text-sm font-semibold text-white">Admin directory</div>
            <div className="text-xs text-slate-400">Create, disable, and reset Admin accounts</div>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-white/[0.03] text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-6 py-3 font-medium">Admin</th>
                <th className="px-6 py-3 font-medium">Role</th>
                <th className="px-6 py-3 font-medium">Status</th>
                <th className="px-6 py-3 font-medium">Created</th>
                <th className="px-6 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {admins.map(row => (
                <tr key={row.user_id} className="border-t border-line/30">
                  <td className="px-6 py-3">
                    <div className="font-medium text-white">{row.display_name}</div>
                    <div className="font-mono text-xs text-slate-400">@{row.username}</div>
                  </td>
                  <td className="px-6 py-3 uppercase tracking-wide text-slate-300">{row.role}</td>
                  <td className="px-6 py-3"><StatusBadge status={row.active ? 'Active' : 'Disabled'} /></td>
                  <td className="px-6 py-3 text-slate-300">{row.created_at ? formatExactTimestamp(row.created_at) : '—'}</td>
                  <td className="px-6 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button
                        tone="secondary"
                        loading={busy === `active:${row.user_id}`}
                        onClick={() => void onToggleActive(row)}
                      >
                        {row.active ? 'Disable' : 'Enable'}
                      </Button>
                      <Button tone="ghost" onClick={() => { setResetError(null); setResetPassword(''); setResetConfirm(''); setResetTarget(row) }}>
                        Reset password
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!admins.length && (
                <tr><td className="px-6 py-8 text-slate-400" colSpan={5}>No Admin accounts loaded.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="surface mt-5 p-6">
        <div className="text-sm font-semibold text-white">Workspace membership</div>
        <p className="mt-1 text-xs text-slate-400">Grant or remove Admin access for projects you belong to. Removing the last Admin on a workspace is blocked.</p>
        <form className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto]" onSubmit={onGrantMembership}>
          <label className="block">
            <span className="label">Workspace</span>
            <select className="input" value={grantWorkspaceId} onChange={e => setGrantWorkspaceId(e.target.value)}>
              <option value="">Select workspace</option>
              {workspaces.map(ws => (
                <option key={ws.workspace_id} value={ws.workspace_id}>{ws.project_name || ws.workspace_id}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Admin</span>
            <select className="input" value={grantUserId} onChange={e => setGrantUserId(e.target.value)}>
              <option value="">Select Admin</option>
              {admins.filter(row => row.active).map(row => (
                <option key={row.user_id} value={row.user_id}>{row.display_name} (@{row.username})</option>
              ))}
            </select>
          </label>
          <div className="flex items-end">
            <Button type="submit" tone="primary" loading={busy === 'grant'} className="w-full md:w-auto">Grant access</Button>
          </div>
        </form>

        <div className="mt-6 space-y-4">
          {workspaces.map(ws => {
            const members = membersByWorkspace.get(ws.workspace_id) || []
            return (
              <div key={ws.workspace_id} className="rounded-xl border border-line/40 bg-white/[0.02] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium text-white">{ws.project_name || ws.workspace_id}</div>
                    <div className="font-mono text-[11px] text-slate-500">{ws.workspace_id}</div>
                  </div>
                  <div className="text-xs text-slate-400">{members.length} Admin{members.length === 1 ? '' : 's'}</div>
                </div>
                <ul className="mt-3 space-y-2">
                  {members.map(member => (
                    <li key={`${member.workspace_id}:${member.user_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-black/20 px-3 py-2 text-sm">
                      <div>
                        <span className="text-slate-100">{member.display_name || member.username || member.user_id}</span>
                        {member.username && <span className="ml-2 font-mono text-xs text-slate-500">@{member.username}</span>}
                      </div>
                      <Button
                        tone="danger"
                        loading={busy === `revoke:${member.user_id}:${member.workspace_id}`}
                        onClick={() => void onRevokeMembership(member)}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                  {!members.length && <li className="text-xs text-slate-500">No members listed for this workspace.</li>}
                </ul>
              </div>
            )
          })}
          {!workspaces.length && (
            <Alert kind="info" title="No workspaces yet">Create a project first, then grant other Admins access here.</Alert>
          )}
        </div>
      </section>

      <Modal
        open={createOpen}
        title="Create Admin"
        description="New Admins sign in with the username and password you set. Password values are not shown again."
        busy={busy === 'create'}
        onClose={() => { if (busy !== 'create') setCreateOpen(false) }}
        onSubmit={onCreateAdmin}
        footer={(
          <>
            <Button tone="ghost" disabled={busy === 'create'} onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button type="submit" tone="primary" loading={busy === 'create'}>Create Admin</Button>
          </>
        )}
      >
        <div className="space-y-3">
          <label className="block"><span className="label">Username</span><input className="input" value={createUsername} onChange={e => setCreateUsername(e.target.value)} required autoComplete="off" /></label>
          <label className="block"><span className="label">Display name</span><input className="input" value={createDisplayName} onChange={e => setCreateDisplayName(e.target.value)} autoComplete="off" /></label>
          <label className="block"><span className="label">Password</span><input className="input" type="password" value={createPassword} onChange={e => setCreatePassword(e.target.value)} required autoComplete="new-password" /></label>
          <label className="block"><span className="label">Confirm password</span><input className="input" type="password" value={createConfirm} onChange={e => setCreateConfirm(e.target.value)} required autoComplete="new-password" /></label>
          {createError && <Alert kind="danger" title="Could not create Admin">{createError}</Alert>}
        </div>
      </Modal>

      <Modal
        open={Boolean(resetTarget)}
        title={resetTarget ? `Reset password · ${resetTarget.username}` : 'Reset password'}
        description="Set a new password for this Admin. The value is never displayed after submit."
        busy={busy === 'reset'}
        onClose={() => { if (busy !== 'reset') setResetTarget(null) }}
        onSubmit={onResetPassword}
        footer={(
          <>
            <Button tone="ghost" disabled={busy === 'reset'} onClick={() => setResetTarget(null)}>Cancel</Button>
            <Button type="submit" tone="primary" loading={busy === 'reset'}>Reset password</Button>
          </>
        )}
      >
        <div className="space-y-3">
          <label className="block"><span className="label">New password</span><input className="input" type="password" value={resetPassword} onChange={e => setResetPassword(e.target.value)} required autoComplete="new-password" /></label>
          <label className="block"><span className="label">Confirm password</span><input className="input" type="password" value={resetConfirm} onChange={e => setResetConfirm(e.target.value)} required autoComplete="new-password" /></label>
          {resetError && <Alert kind="danger" title="Could not reset password">{resetError}</Alert>}
        </div>
      </Modal>
    </>
  )
}
