import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import ts from 'typescript'

import fixture from './fixtures/plan-adjustment-proposal-interaction-cases.json' with { type: 'json' }


function loadTypeScriptModule (relativePath, moduleOverrides = {}) {
  const sourceUrl = new URL(relativePath, import.meta.url)
  const sourcePath = fileURLToPath(sourceUrl)
  const output = ts.transpileModule(readFileSync(sourcePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: sourcePath
  }).outputText
  const loaded = { exports: {} }
  const evaluate = new Function('exports', 'module', 'require', output)
  const fallbackRequire = createRequire(sourceUrl)
  evaluate(loaded.exports, loaded, specifier => (
    Object.hasOwn(moduleOverrides, specifier)
      ? moduleOverrides[specifier]
      : fallbackRequire(specifier)
  ))
  return loaded.exports
}

const interaction = loadTypeScriptModule('../src/core/proposal-interaction.ts')


class MockApiRequestError extends Error {
  constructor (message, statusCode, payload) {
    super(message)
    this.statusCode = statusCode
    this.code = typeof payload.code === 'string' ? payload.code : undefined
    this.payload = payload
  }
}

function proposalServiceHarness (apiRequest) {
  const records = new Map()
  const storage = {
    getPendingProposalDecision: proposalId => records.get(proposalId) || null,
    savePendingProposalDecision: record => records.set(record.proposal_id, record),
    clearPendingProposalDecision: proposalId => records.delete(proposalId)
  }
  const service = loadTypeScriptModule('../src/services/proposals.ts', {
    '../core/request': {
      ApiRequestError: MockApiRequestError,
      apiRequest
    },
    '../core/proposal-interaction': interaction,
    '../core/storage': storage
  })
  return { records, service }
}

function terminalDecision (proposalId, status = 'applied') {
  return {
    id: proposalId,
    proposal_type: 'plan_adjustment_v1',
    status,
    version: 2,
    applied: status === 'applied',
    payload_fingerprint: 'a'.repeat(64),
    result_plan_id: status === 'applied' ? 'plan-result' : null,
    result_plan_fingerprint: status === 'applied' ? 'b'.repeat(64) : null,
    decided_at: '2026-08-25T00:00:00Z'
  }
}


test('runtime recovery projector satisfies every fixed interaction case', () => {
  for (const item of fixture.decision_recovery_cases) {
    const actual = interaction.projectProposalDecisionRecovery({
      transportResult: item.transport_result,
      responseStatus: item.response_status,
      errorCode: item.error_code
    })
    assert.deepEqual(actual, item.expected, item.case_id)
  }
})

test('runtime status projector satisfies every fixed presentation case', () => {
  for (const item of fixture.status_projection_cases) {
    const actual = interaction.projectProposalStatus(
      item.server_status,
      item.local_expiry_state
    )
    assert.deepEqual(actual, item.expected, item.case_id)
  }
})

test('local expiry is only a conservative guard for pending proposals', () => {
  const expiry = '2026-08-25T12:00:00Z'
  assert.equal(
    interaction.proposalLocalExpiryState(
      'pending_confirmation',
      expiry,
      Date.parse('2026-08-25T11:59:59Z')
    ),
    'before'
  )
  assert.equal(
    interaction.proposalLocalExpiryState(
      'pending_confirmation',
      expiry,
      Date.parse(expiry)
    ),
    'at_or_after'
  )
  assert.equal(
    interaction.proposalLocalExpiryState(
      'applied',
      expiry,
      Date.parse('2026-08-26T00:00:00Z')
    ),
    'irrelevant'
  )
})

test('decision journal creates the request id before the first attempt', () => {
  const resolved = interaction.resolveProposalDecisionJournal(
    null,
    'proposal-0001',
    'confirm',
    1,
    'proposal-request-0001',
    1_700_000_000_000
  )

  assert.equal(resolved.outcome, 'created')
  assert.deepEqual(resolved.record, {
    schema_version: '1.0.0',
    proposal_id: 'proposal-0001',
    action: 'confirm',
    expected_version: 1,
    client_request_id: 'proposal-request-0001',
    created_at: 1_700_000_000_000,
    last_attempt_at: null,
    attempt_count: 0
  })

  const attempted = interaction.markProposalDecisionAttempt(
    resolved.record,
    1_700_000_000_100
  )
  assert.equal(attempted.client_request_id, 'proposal-request-0001')
  assert.equal(attempted.attempt_count, 1)
  assert.equal(attempted.last_attempt_at, 1_700_000_000_100)
})

test('manual retry reuses the exact request id and increments attempts', () => {
  const first = interaction.resolveProposalDecisionJournal(
    null,
    'proposal-retry',
    'reject',
    3,
    'proposal-request-stable',
    100
  ).record
  const attempted = interaction.markProposalDecisionAttempt(first, 110)
  const retried = interaction.resolveProposalDecisionJournal(
    attempted,
    'proposal-retry',
    'reject',
    3,
    'proposal-request-new-must-be-ignored',
    120
  )

  assert.equal(retried.outcome, 'reused')
  assert.equal(retried.record.client_request_id, 'proposal-request-stable')
  assert.equal(
    interaction.markProposalDecisionAttempt(retried.record, 130).attempt_count,
    2
  )
})

test('a different action or version cannot replace an unresolved journal', () => {
  const existing = interaction.resolveProposalDecisionJournal(
    null,
    'proposal-conflict',
    'confirm',
    1,
    'proposal-request-conflict',
    100
  ).record

  for (const [action, version] of [['reject', 1], ['confirm', 2]]) {
    const resolution = interaction.resolveProposalDecisionJournal(
      existing,
      'proposal-conflict',
      action,
      version,
      'proposal-request-replacement',
      200
    )
    assert.equal(resolution.outcome, 'conflict')
    assert.equal(resolution.record.client_request_id, existing.client_request_id)
  }
})

test('stored journal validation rejects malformed or short request ids', () => {
  const valid = interaction.resolveProposalDecisionJournal(
    null,
    'proposal-valid',
    'confirm',
    1,
    'proposal-request-valid',
    100
  ).record

  assert.equal(interaction.isPendingProposalDecision(valid), true)
  assert.equal(interaction.isPendingProposalDecision({
    ...valid,
    client_request_id: 'short'
  }), false)
  assert.equal(interaction.isPendingProposalDecision({
    ...valid,
    expected_version: 0
  }), false)
  assert.equal(interaction.isPendingProposalDecision({
    ...valid,
    attempt_count: -1
  }), false)
})

test('request ids meet the backend length contract deterministically', () => {
  const requestId = interaction.createProposalDecisionRequestId(123456789, 0.5)
  assert.match(requestId, /^proposal-[a-z0-9]+-[a-z0-9]+$/)
  assert.ok(requestId.length >= 8)
  assert.ok(requestId.length <= 120)
  assert.equal(
    interaction.createProposalDecisionRequestId(123456789, 0.5),
    requestId
  )
})

test('client coalesces a local double tap and persists before its only POST', async () => {
  let releaseRequest
  const calls = []
  const requestPending = new Promise(resolve => {
    releaseRequest = resolve
  })
  const harness = proposalServiceHarness(async (path, options) => {
    calls.push({ path, options })
    return requestPending
  })

  const first = harness.service.proposalsApi.confirm('proposal-double', 1)
  const second = harness.service.proposalsApi.confirm('proposal-double', 1)

  assert.equal(first, second)
  assert.equal(calls.length, 1)
  assert.equal(harness.records.get('proposal-double').attempt_count, 1)
  assert.equal(
    calls[0].options.data.client_request_id,
    harness.records.get('proposal-double').client_request_id
  )

  releaseRequest(terminalDecision('proposal-double'))
  await first
  assert.equal(harness.records.has('proposal-double'), false)
})

test('uncertain response keeps one id for an explicit manual retry', async () => {
  const calls = []
  let fail = true
  const harness = proposalServiceHarness(async (path, options) => {
    calls.push({ path, options })
    if (fail) throw new Error('callContainer:fail timeout')
    return terminalDecision('proposal-timeout')
  })

  await assert.rejects(
    harness.service.proposalsApi.confirm('proposal-timeout', 1),
    error => error.kind === 'uncertain' &&
      error.recovery.next_step === 'query_status'
  )
  assert.equal(calls.length, 1)
  const firstRequestId = calls[0].options.data.client_request_id
  assert.equal(
    harness.records.get('proposal-timeout').client_request_id,
    firstRequestId
  )

  fail = false
  await harness.service.proposalsApi.confirm('proposal-timeout', 1)
  assert.equal(calls.length, 2)
  assert.equal(calls[1].options.data.client_request_id, firstRequestId)
  assert.equal(harness.records.has('proposal-timeout'), false)
})

test('business error mapping keeps only explicitly retryable journals', async () => {
  const disabled = proposalServiceHarness(async () => {
    throw new MockApiRequestError('temporarily disabled', 503, {
      code: 'proposal_feature_disabled',
      message: 'temporarily disabled'
    })
  })
  await assert.rejects(
    disabled.service.proposalsApi.reject('proposal-disabled', 1),
    error => error.code === 'proposal_feature_disabled' &&
      error.recovery.journal === 'keep' &&
      error.recovery.retry_request_id === 'reuse'
  )
  assert.equal(disabled.records.has('proposal-disabled'), true)

  const conflict = proposalServiceHarness(async () => {
    throw new MockApiRequestError('version conflict', 409, {
      code: 'proposal_version_conflict',
      message: 'version conflict'
    })
  })
  await assert.rejects(
    conflict.service.proposalsApi.confirm('proposal-conflict-clear', 1),
    error => error.code === 'proposal_version_conflict' &&
      error.recovery.journal === 'clear' &&
      error.recovery.next_step === 'query_status'
  )
  assert.equal(conflict.records.has('proposal-conflict-clear'), false)
})
