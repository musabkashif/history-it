import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function source(relative: string) {
  return readFileSync(join(root, relative), 'utf8')
}

test('session response includes Admin identity fields', () => {
  const api = source('src/lib/api.ts')
  assert.match(api, /user_id\?:/)
  assert.match(api, /display_name\?:/)
  assert.match(api, /role\?:/)
})

test('last-job storage is scoped by user_id', () => {
  const app = source('src/main.tsx')
  assert.match(app, /historyit\.lastJobId\.\$\{userId\}|lastJobStorageKey\(userId\)/)
  assert.match(app, /function lastJobStorageKey/)
})

test('shell shows Admin identity and active job context', () => {
  const layout = source('src/components/layout.tsx')
  assert.match(layout, /displayName/)
  assert.match(layout, /jobId/)
  assert.match(layout, /jobStatus/)
  assert.match(layout, /Admin console/)
})

test('Phase 1 UI primitives remain present', () => {
  assert.match(source('src/components/Modal.tsx'), /export function Modal/)
  assert.match(source('src/components/loading.tsx'), /export function PageLoading/)
  assert.match(source('src/lib/formatTime.ts'), /formatRelativeTimestamp/)
})
