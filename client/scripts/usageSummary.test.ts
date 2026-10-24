import assert from 'node:assert/strict'
import { test } from 'node:test'
import { provenCostLabel, tokenTotalLabel, usageSummaryLine } from '../src/lib/usageSummary.ts'

const empty = {
  proven_cost_usd: null,
  unknown_event_count: 0,
  request_count: 0,
  cache_write_tokens: null,
  cache_read_tokens: null,
  cache_write_known_tokens: null,
  cache_read_known_tokens: null,
  cache_write_unknown_count: 0,
  cache_read_unknown_count: 0,
}

test('missing historical cost is unknown rather than zero', () => {
  assert.equal(provenCostLabel(null), 'No usage events')
  assert.equal(provenCostLabel(empty), 'No usage events')
  assert.equal(provenCostLabel({ ...empty, request_count: 4, unknown_event_count: 4 }), 'Unknown')
  assert.equal(provenCostLabel({ ...empty, request_count: 1, proven_cost_usd: '0.000000' }), '$0.00 estimate')
  assert.equal(provenCostLabel({ ...empty, request_count: 2, unknown_event_count: 1, proven_cost_usd: '4.000000' }), '$4.00 estimate')
  assert.match(usageSummaryLine({ ...empty, request_count: 4, unknown_event_count: 4 }), /Unknown/)
  assert.doesNotMatch(usageSummaryLine({ ...empty, request_count: 4, unknown_event_count: 4 }), /\$0/)
})

test('cache totals stay unknown when a category was not recorded', () => {
  assert.equal(tokenTotalLabel(null, null, 0), '—')
  assert.equal(tokenTotalLabel(null, null, 3), 'Unknown')
  assert.equal(tokenTotalLabel(null, 1200, 2), '1,200 known · 2 unknown')
  assert.equal(tokenTotalLabel(1200, 1200, 0), '1,200')
})
