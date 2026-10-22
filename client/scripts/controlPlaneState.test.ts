import assert from 'node:assert/strict'
import { test } from 'node:test'
import { controlMessage, controlView, databaseWarning, jobScreen, resumeAllowed, sectionLabel, shown, sourceLabel } from '../src/lib/controlPlaneState.ts'

test('loading, error, empty, offline, and disabled stay distinct', () => {
  assert.equal(controlView({ loading: true, empty: true, offline: true }), 'loading')
  assert.equal(controlView({ loading: false, offline: true, requestFailed: true }), 'offline')
  assert.equal(controlView({ loading: false, requestFailed: true }), 'error')
  assert.equal(controlView({ loading: false, disabled: true, empty: true }), 'disabled')
  assert.equal(controlView({ loading: false, empty: true }), 'empty')
  assert.equal(controlView({ loading: false }), 'ready')
  assert.match(controlMessage('loading'), /Loading/)
  assert.match(controlMessage('offline'), /Google Sheets/)
  assert.match(controlMessage('disabled'), /Google Sheets/)
  assert.match(controlMessage('error'), /could not load/)
  assert.match(controlMessage('empty'), /No records/)
})

test('missing values render as unknown and historical jobs do not resume', () => {
  assert.equal(shown(null), 'unknown')
  assert.equal(shown('  '), 'unknown')
  assert.equal(shown('F04'), 'F04')
  assert.equal(sourceLabel('historical'), 'Historical')
  assert.equal(sourceLabel('imported'), 'Imported')
  assert.equal(resumeAllowed({ source: 'historical', job_status: 'paused' }), false)
  assert.equal(resumeAllowed({ source: 'live', job_status: 'paused' }), true)
  assert.equal(resumeAllowed({ source: 'live', job_status: 'completed', resume_allowed: false }), false)
  assert.equal(jobScreen('live'), 'sheets')
  assert.equal(jobScreen('historical'), 'record')
  assert.equal(jobScreen('imported'), 'record')
  assert.equal(jobScreen(null), 'record')
  assert.equal(sectionLabel(0, 'folders'), 'No folders recorded.')
  assert.equal(sectionLabel(2, 'folders'), null)
})

test('a database outage warns without marking the Sheets service offline', () => {
  assert.equal(databaseWarning({ status: 'ok', database_message: 'MongoDB is not configured' }), null)
  assert.match(databaseWarning({ status: 'ok', database_message: 'MongoDB is configured but unavailable' }) || '', /unavailable/)
  assert.equal(databaseWarning({ status: 'offline', database_message: 'MongoDB is configured but unavailable' }), null)
  assert.equal(databaseWarning(null), null)
})
