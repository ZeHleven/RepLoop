import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const fixture = JSON.parse(readFileSync(
  new URL(
    './fixtures/plan-adjustment-proposal-interaction-cases.json',
    import.meta.url
  ),
  'utf8'
))

const STATUS_CASE_KEYS = new Set([
  'case_id',
  'server_status',
  'local_expiry_state',
  'expected'
])
const STATUS_EXPECTED_KEYS = new Set([
  'label',
  'tone',
  'terminal',
  'actions',
  'detail_required',
  'refresh_required',
  'may_claim_applied'
])
const DECISION_CASE_KEYS = new Set([
  'case_id',
  'transport_result',
  'response_status',
  'error_code',
  'expected'
])
const DECISION_EXPECTED_KEYS = new Set([
  'journal',
  'next_step',
  'retry_request_id',
  'automatic_post_count',
  'may_claim_terminal'
])
const EXPIRATION_CASE_KEYS = new Set([
  'case_id',
  'authority',
  'time_position',
  'server_status',
  'expected'
])
const EXPIRATION_EXPECTED_KEYS = new Set([
  'ui_status',
  'actions_enabled',
  'query_status',
  'may_claim_expired'
])
const CONCURRENCY_CASE_KEYS = new Set([
  'case_id',
  'mode',
  'gestures',
  'clients',
  'observed_get_status',
  'expected'
])
const CONCURRENCY_EXPECTED_KEYS = new Set([
  'post_attempts',
  'automatic_reposts',
  'visible_successes',
  'visible_conflicts',
  'allowed_observed_statuses',
  'business_write_count_max',
  'new_plan_count_max',
  'active_plan_count'
])


function assertSameKeys (value, expected) {
  assert.ok(value && typeof value === 'object' && !Array.isArray(value))
  assert.deepEqual(new Set(Object.keys(value)), expected)
}


function projectStatus (serverStatus, localExpiryState) {
  if (serverStatus === 'pending_confirmation') {
    if (localExpiryState === 'at_or_after') {
      return {
        label: '正在核实提案状态',
        tone: 'neutral',
        terminal: false,
        actions: [],
        detail_required: true,
        refresh_required: true,
        may_claim_applied: false
      }
    }
    return {
      label: '待你确认',
      tone: 'attention',
      terminal: false,
      actions: ['confirm', 'reject'],
      detail_required: true,
      refresh_required: false,
      may_claim_applied: false
    }
  }

  const terminal = {
    applied: ['已应用', 'success', true],
    rejected: ['已拒绝', 'neutral', false],
    expired: ['已过期', 'neutral', false],
    stale: ['计划情况已变化', 'warning', false],
    failed: ['调整未完成', 'warning', false],
    missing: ['提案不可用', 'neutral', false]
  }[serverStatus]
  if (!terminal) throw new Error(`unknown proposal status: ${serverStatus}`)
  return {
    label: terminal[0],
    tone: terminal[1],
    terminal: true,
    actions: [],
    detail_required: serverStatus !== 'missing',
    refresh_required: false,
    may_claim_applied: terminal[2]
  }
}


function recoverDecision (transportResult, responseStatus, errorCode) {
  if (transportResult === 'success') {
    if (!['applied', 'rejected'].includes(responseStatus)) {
      throw new Error('successful decision requires a terminal response')
    }
    return {
      journal: 'clear',
      next_step: 'render_terminal',
      retry_request_id: 'none',
      automatic_post_count: 0,
      may_claim_terminal: true
    }
  }
  if (transportResult === 'uncertain') {
    return {
      journal: 'keep',
      next_step: 'query_status',
      retry_request_id: 'reuse',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }
  if (transportResult === 'validation_error') {
    return {
      journal: 'clear',
      next_step: 'fix_client_contract',
      retry_request_id: 'none',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }
  if (transportResult !== 'business_error') {
    throw new Error(`unknown transport result: ${transportResult}`)
  }
  if (errorCode === 'proposal_feature_disabled') {
    return {
      journal: 'keep',
      next_step: 'wait_for_manual_retry',
      retry_request_id: 'reuse',
      automatic_post_count: 0,
      may_claim_terminal: false
    }
  }
  return {
    journal: 'clear',
    next_step: errorCode === 'proposal_not_found'
      ? 'render_unavailable'
      : 'query_status',
    retry_request_id: 'none',
    automatic_post_count: 0,
    may_claim_terminal: false
  }
}


function projectExpiration (authority, timePosition, serverStatus) {
  if (authority === 'page_foreground') {
    return {
      ui_status: 'pending_verification',
      actions_enabled: false,
      query_status: true,
      may_claim_expired: false
    }
  }
  if (authority === 'client_clock' && timePosition !== 'before') {
    return {
      ui_status: 'pending_verification',
      actions_enabled: false,
      query_status: true,
      may_claim_expired: false
    }
  }
  if (authority === 'server_response' && serverStatus === 'expired') {
    return {
      ui_status: 'expired',
      actions_enabled: false,
      query_status: false,
      may_claim_expired: true
    }
  }
  return {
    ui_status: 'pending_confirmation',
    actions_enabled: true,
    query_status: false,
    may_claim_expired: false
  }
}


function projectConcurrency (mode, gestures, observedGetStatus) {
  const shared = {
    automatic_reposts: 0,
    active_plan_count: 1
  }
  if (mode === 'local_double_tap') {
    return {
      ...shared,
      post_attempts: Math.min(gestures, 1),
      visible_successes: 1,
      visible_conflicts: 0,
      allowed_observed_statuses: ['applied'],
      business_write_count_max: 1,
      new_plan_count_max: 1
    }
  }
  if (mode === 'same_confirm_multi_client') {
    return {
      ...shared,
      post_attempts: gestures,
      visible_successes: 2,
      visible_conflicts: 0,
      allowed_observed_statuses: ['applied'],
      business_write_count_max: 1,
      new_plan_count_max: 1
    }
  }
  if (mode === 'mixed_actions_multi_client') {
    return {
      ...shared,
      post_attempts: gestures,
      visible_successes: 1,
      visible_conflicts: 1,
      allowed_observed_statuses: ['applied', 'rejected'],
      business_write_count_max: 1,
      new_plan_count_max: 1
    }
  }
  if (mode === 'lost_response_terminal_get') {
    if (observedGetStatus !== 'applied') {
      throw new Error('terminal recovery requires applied GET evidence')
    }
    return {
      ...shared,
      post_attempts: gestures,
      visible_successes: 1,
      visible_conflicts: 0,
      allowed_observed_statuses: ['applied'],
      business_write_count_max: 1,
      new_plan_count_max: 1
    }
  }
  if (mode === 'lost_response_pending_get') {
    if (observedGetStatus !== 'pending_confirmation') {
      throw new Error('pending recovery requires pending GET evidence')
    }
    return {
      ...shared,
      post_attempts: gestures,
      visible_successes: 0,
      visible_conflicts: 0,
      allowed_observed_statuses: ['pending_confirmation'],
      business_write_count_max: 0,
      new_plan_count_max: 0
    }
  }
  throw new Error(`unknown concurrency mode: ${mode}`)
}


test('fixture shape is versioned, strict, and case identifiers are unique', () => {
  assert.equal(fixture.fixture_version, '1.0.0')
  const groups = [
    [fixture.status_projection_cases, STATUS_CASE_KEYS, STATUS_EXPECTED_KEYS],
    [fixture.decision_recovery_cases, DECISION_CASE_KEYS, DECISION_EXPECTED_KEYS],
    [fixture.expiration_cases, EXPIRATION_CASE_KEYS, EXPIRATION_EXPECTED_KEYS],
    [fixture.concurrency_cases, CONCURRENCY_CASE_KEYS, CONCURRENCY_EXPECTED_KEYS]
  ]
  const identifiers = []
  for (const [cases, caseKeys, expectedKeys] of groups) {
    assert.ok(Array.isArray(cases) && cases.length > 0)
    for (const item of cases) {
      assertSameKeys(item, caseKeys)
      assertSameKeys(item.expected, expectedKeys)
      identifiers.push(item.case_id)
    }
  }
  assert.equal(new Set(identifiers).size, identifiers.length)
})


test('status cases disable terminal actions and never claim applied locally', () => {
  for (const item of fixture.status_projection_cases) {
    assert.deepEqual(
      projectStatus(item.server_status, item.local_expiry_state),
      item.expected,
      item.case_id
    )
  }
  const actionable = fixture.status_projection_cases.filter(
    item => item.expected.actions.length > 0
  )
  assert.deepEqual(actionable.map(item => item.case_id), [
    'pending.before_local_expiry'
  ])
  const claimsApplied = fixture.status_projection_cases.filter(
    item => item.expected.may_claim_applied
  )
  assert.deepEqual(claimsApplied.map(item => item.server_status), ['applied'])
})


test('decision recovery preserves idempotency without automatic mutation retry', () => {
  for (const item of fixture.decision_recovery_cases) {
    assert.deepEqual(
      recoverDecision(
        item.transport_result,
        item.response_status,
        item.error_code
      ),
      item.expected,
      item.case_id
    )
    assert.equal(item.expected.automatic_post_count, 0, item.case_id)
  }
  const uncertain = fixture.decision_recovery_cases.find(
    item => item.case_id === 'uncertain.timeout'
  )
  assert.equal(uncertain.expected.journal, 'keep')
  assert.equal(uncertain.expected.retry_request_id, 'reuse')
})


test('expiration cases treat client time as a guard and server time as authority', () => {
  for (const item of fixture.expiration_cases) {
    assert.deepEqual(
      projectExpiration(
        item.authority,
        item.time_position,
        item.server_status
      ),
      item.expected,
      item.case_id
    )
  }
  const localExpiryCases = fixture.expiration_cases.filter(
    item => item.authority === 'client_clock' &&
      item.time_position !== 'before'
  )
  assert.ok(localExpiryCases.every(
    item => item.expected.query_status &&
      !item.expected.may_claim_expired &&
      !item.expected.actions_enabled
  ))
})


test('concurrency cases allow one write and prohibit automatic reposts', () => {
  for (const item of fixture.concurrency_cases) {
    assert.deepEqual(
      projectConcurrency(
        item.mode,
        item.gestures,
        item.observed_get_status
      ),
      item.expected,
      item.case_id
    )
    assert.equal(item.expected.automatic_reposts, 0, item.case_id)
    assert.ok(item.expected.business_write_count_max <= 1, item.case_id)
    assert.ok(item.expected.new_plan_count_max <= 1, item.case_id)
    assert.equal(item.expected.active_plan_count, 1, item.case_id)
  }
})


test('safety invariants require explicit full-detail authorization', () => {
  assert.deepEqual(fixture.safety_invariants, {
    confirmation_surface: 'dedicated_detail_page',
    full_before_after_required: true,
    identity_authority: 'jwt_current_user',
    server_status_authoritative: true,
    server_expiry_authoritative: true,
    automatic_confirm_allowed: false,
    automatic_decision_retry_allowed: false,
    optimistic_applied_allowed: false,
    request_id_created_before_post: true,
    uncertain_retry_reuses_request_id: true,
    terminal_ui_requires_server_evidence: true
  })
  const serialized = JSON.stringify(fixture).toLowerCase()
  for (const forbidden of [
    'authorization',
    'bearer ',
    'access_token',
    'refresh_token',
    'password'
  ]) {
    assert.equal(serialized.includes(forbidden), false, forbidden)
  }
})
