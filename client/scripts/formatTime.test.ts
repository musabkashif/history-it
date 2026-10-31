import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  activityPhrase,
  formatExactTimestamp,
  formatRelativeTimestamp,
  parseInstant,
  pickJobActivity,
} from '../src/lib/formatTime.ts'

const now = Date.parse('2026-10-30T12:00:00.000Z')

test('relative timestamps for recent and older values', () => {
  assert.equal(formatRelativeTimestamp('2026-10-30T11:59:30.000Z', now), 'just now')
  assert.match(formatRelativeTimestamp('2026-10-30T10:00:00.000Z', now), /2 hours ago/)
  assert.match(formatRelativeTimestamp('2026-10-29T12:00:00.000Z', now), /yesterday|1 day ago|24 hours ago/)
})

test('exact timestamp is available for accessibility titles', () => {
  const exact = formatExactTimestamp('2026-10-30T10:00:00.000Z', now)
  assert.ok(exact.length > 0)
  const phrase = activityPhrase('Created', '2026-10-30T10:00:00.000Z', now)
  assert.ok(phrase)
  assert.match(phrase!.text, /^Created /)
  assert.equal(phrase!.title, `Created ${exact}`)
})

test('missing timestamps are omitted safely', () => {
  assert.deepEqual(parseInstant(null), { date: null, iso: '', valid: false, reason: 'missing' })
  assert.deepEqual(parseInstant(''), { date: null, iso: '', valid: false, reason: 'missing' })
  assert.equal(formatRelativeTimestamp(undefined, now), '')
  assert.equal(activityPhrase('Updated', null, now), null)
})

test('invalid and absurd future timestamps are rejected', () => {
  assert.equal(parseInstant('not-a-date', now).valid, false)
  assert.equal(parseInstant('not-a-date', now).reason, 'invalid')
  assert.equal(formatExactTimestamp('totally-invalid', now), '')
  assert.equal(parseInstant('2199-01-01T00:00:00.000Z', now).valid, false)
})

test('job activity prefers updated_at without inventing fields', () => {
  assert.deepEqual(
    pickJobActivity({ created_at: 'a', updated_at: 'b', started_at: 'c', completed_at: 'd' }),
    { created: 'a', activity: 'b', activityLabel: 'Updated' },
  )
  assert.deepEqual(
    pickJobActivity({ created_at: 'a', completed_at: 'd' }),
    { created: 'a', activity: 'd', activityLabel: 'Last activity' },
  )
  assert.deepEqual(
    pickJobActivity({ created_at: 'a' }),
    { created: 'a', activity: null, activityLabel: 'Updated' },
  )
})
