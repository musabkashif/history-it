import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function source(relative: string) {
  return readFileSync(join(root, relative), 'utf8')
}

test('account API helpers cover directory and memberships', () => {
  const api = source('src/lib/api.ts')
  assert.match(api, /account:\s*\(\)\s*=>/)
  assert.match(api, /listAdmins:/)
  assert.match(api, /createAdmin:/)
  assert.match(api, /resetAdminPassword:/)
  assert.match(api, /setAdminActive:/)
  assert.match(api, /listMemberships:/)
  assert.match(api, /grantMembership:/)
  assert.match(api, /revokeMembership:/)
  assert.doesNotMatch(api, /password_hash/)
  assert.doesNotMatch(api, /VITE_API_TOKEN/)
})

test('Account page covers current account, password, directory, and memberships', () => {
  const page = source('src/pages/Account.tsx')
  assert.match(page, /Current account/)
  assert.match(page, /Change password/)
  assert.match(page, /Admin directory/)
  assert.match(page, /Workspace membership/)
  assert.match(page, /Cannot disable the last active Admin/)
  assert.match(page, /Cannot remove the last Admin from this workspace/)
  assert.match(page, /type="password"/)
  assert.doesNotMatch(page, /password_hash/)
  assert.doesNotMatch(page, /\$argon2/)
  assert.match(page, /PageLoading/)
  assert.match(page, /PageBreadcrumbs/)
})

test('shell navigates to Account settings', () => {
  const layout = source('src/components/layout.tsx')
  assert.match(layout, /id: 'account'/)
  assert.match(layout, /label: 'Account'/)
  const app = source('src/main.tsx')
  assert.match(app, /AccountPage/)
  assert.match(app, /view === 'account'/)
})

test('Guide settings page remains available', () => {
  assert.match(source('src/pages/Settings.tsx'), /Operator guide/)
  assert.match(source('src/main.tsx'), /SettingsPage/)
})
