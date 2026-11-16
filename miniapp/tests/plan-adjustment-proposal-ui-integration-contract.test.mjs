import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import ts from 'typescript'


const fixture = JSON.parse(readFileSync(
  new URL(
    './fixtures/plan-adjustment-proposal-ui-integration-cases.json',
    import.meta.url
  ),
  'utf8'
))

function loadTypeScriptModule (relativePath) {
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
  const evaluate = new Function('exports', 'module', output)
  evaluate(loaded.exports, loaded)
  return loaded.exports
}

const runtime = loadTypeScriptModule('../src/core/proposal-decision-ui.ts')
const displayRuntime = loadTypeScriptModule('../src/core/proposal-display.ts')

const CONFIRMATION_KEYS = new Set([
  'case_id',
  'action',
  'server_status',
  'local_expiry_state',
  'detail_reviewed',
  'confirmation_required',
  'user_choice',
  'existing_journal',
  'expected'
])
const CONFIRMATION_EXPECTED_KEYS = new Set([
  'modal',
  'post_attempts',
  'lock',
  'next_ui_state',
  'request_id'
])
const LOCK_KEYS = new Set([
  'case_id',
  'event',
  'initial_lock',
  'completion',
  'expected'
])
const LOCK_EXPECTED_KEYS = new Set([
  'new_post_attempts',
  'new_get_attempts',
  'ignored_gestures',
  'next_lock',
  'next_ui_state'
])
const UNCERTAIN_KEYS = new Set([
  'case_id',
  'action',
  'post_result',
  'get_result',
  'expected'
])
const UNCERTAIN_EXPECTED_KEYS = new Set([
  'automatic_post_attempts',
  'get_attempts',
  'journal',
  'ui_state',
  'label',
  'manual_retry_visible',
  'may_claim_applied'
])
const RETRY_KEYS = new Set([
  'case_id',
  'journal',
  'authoritative_status',
  'user_choice',
  'expected'
])
const RETRY_EXPECTED_KEYS = new Set([
  'modal_required',
  'post_attempts',
  'request_id',
  'new_request_id_count',
  'next_ui_state'
])
const ERROR_KEYS = new Set([
  'case_id',
  'error_code',
  'recovery',
  'get_status',
  'expected'
])
const ERROR_EXPECTED_KEYS = new Set([
  'get_attempts',
  'journal',
  'ui_state',
  'label',
  'tone',
  'actions',
  'automatic_post_attempts',
  'may_claim_applied'
])


function assertSameKeys (value, keys) {
  assert.ok(value && typeof value === 'object' && !Array.isArray(value))
  assert.deepEqual(new Set(Object.keys(value)), keys)
}

function projectConfirmation (item) {
  if (
    item.server_status !== 'pending_confirmation' ||
    item.local_expiry_state !== 'before'
  ) {
    return {
      modal: 'not_shown',
      post_attempts: 0,
      lock: 'not_acquired',
      next_ui_state: 'refresh_required',
      request_id: 'none'
    }
  }
  if (!item.detail_reviewed) {
    return {
      modal: 'blocked_until_full_detail',
      post_attempts: 0,
      lock: 'not_acquired',
      next_ui_state: 'review_required',
      request_id: 'none'
    }
  }
  const modal = item.confirmation_required
    ? item.action === 'confirm'
      ? 'confirm_plan_adjustment'
      : 'confirm_rejection'
    : 'not_required'
  if (item.user_choice === 'cancel') {
    return {
      modal,
      post_attempts: 0,
      lock: 'not_acquired',
      next_ui_state: 'pending_confirmation',
      request_id: 'none'
    }
  }
  return {
    modal,
    post_attempts: 1,
    lock: 'acquired',
    next_ui_state: 'submitting',
    request_id: item.existing_journal === 'reusable' ? 'reuse' : 'create'
  }
}

function projectLock (item) {
  if (item.completion === 'none') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 0,
      ignored_gestures: 1,
      next_lock: item.initial_lock,
      next_ui_state: 'submitting'
    }
  }
  if (item.completion === 'applied') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 0,
      ignored_gestures: 0,
      next_lock: 'unlocked',
      next_ui_state: 'applied'
    }
  }
  if (item.completion === 'uncertain') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 1,
      ignored_gestures: 0,
      next_lock: 'verification_in_flight',
      next_ui_state: 'verifying'
    }
  }
  if (item.completion === 'feature_disabled') {
    return {
      new_post_attempts: 0,
      new_get_attempts: 0,
      ignored_gestures: 0,
      next_lock: 'unlocked',
      next_ui_state: 'manual_retry_available'
    }
  }
  throw new Error(`unknown lock completion: ${item.completion}`)
}

function projectUncertain (item) {
  assert.equal(item.post_result, 'uncertain')
  const shared = {
    automatic_post_attempts: 0,
    get_attempts: 1,
    may_claim_applied: false
  }
  const terminal = {
    applied: ['applied', '已应用', true],
    rejected: ['rejected', '已拒绝', false],
    expired: ['expired', '已过期', false]
  }[item.get_result]
  if (terminal) {
    return {
      ...shared,
      journal: 'clear',
      ui_state: terminal[0],
      label: terminal[1],
      manual_retry_visible: false,
      may_claim_applied: terminal[2]
    }
  }
  if (item.get_result === 'pending_confirmation') {
    return {
      ...shared,
      journal: 'keep',
      ui_state: 'retry_confirmation_required',
      label: '上次操作结果未确认',
      manual_retry_visible: true
    }
  }
  if (item.get_result === 'transport_error') {
    return {
      ...shared,
      journal: 'keep',
      ui_state: 'verifying',
      label: '正在核实提案状态',
      manual_retry_visible: false
    }
  }
  throw new Error(`unknown uncertain GET result: ${item.get_result}`)
}

function projectManualRetry (item) {
  if (item.authoritative_status !== 'pending_confirmation') {
    return {
      modal_required: false,
      post_attempts: 0,
      request_id: 'none',
      new_request_id_count: 0,
      next_ui_state: item.authoritative_status
    }
  }
  if (item.journal === 'none') {
    return {
      modal_required: false,
      post_attempts: 0,
      request_id: 'none',
      new_request_id_count: 0,
      next_ui_state: 'fresh_decision_required'
    }
  }
  if (item.user_choice === 'cancel') {
    return {
      modal_required: true,
      post_attempts: 0,
      request_id: 'none',
      new_request_id_count: 0,
      next_ui_state: 'retry_confirmation_required'
    }
  }
  return {
    modal_required: true,
    post_attempts: 1,
    request_id: 'reuse',
    new_request_id_count: 0,
    next_ui_state: 'submitting'
  }
}

function projectErrorPresentation (item) {
  const shared = {
    automatic_post_attempts: 0,
    may_claim_applied: false
  }
  if (item.recovery === 'render_unavailable') {
    return {
      ...shared,
      get_attempts: 0,
      journal: 'clear',
      ui_state: 'unavailable',
      label: '提案不可用',
      tone: 'neutral',
      actions: []
    }
  }
  if (item.recovery === 'wait_for_manual_retry') {
    return {
      ...shared,
      get_attempts: 0,
      journal: 'keep',
      ui_state: 'manual_retry_available',
      label: '操作暂时不可用',
      tone: 'warning',
      actions: ['manual_retry_same_request']
    }
  }
  if (item.recovery === 'fix_client_contract') {
    return {
      ...shared,
      get_attempts: 0,
      journal: 'clear',
      ui_state: 'client_update_required',
      label: '客户端版本暂不支持此操作',
      tone: 'warning',
      actions: []
    }
  }
  assert.equal(item.recovery, 'query_status')
  assert.ok(item.get_status)
  const terminal = {
    applied: ['已应用', 'success', true],
    rejected: ['已拒绝', 'neutral', false],
    expired: ['已过期', 'neutral', false],
    stale: ['计划情况已变化', 'warning', false],
    failed: ['调整未完成', 'warning', false]
  }[item.get_status]
  if (terminal) {
    return {
      ...shared,
      get_attempts: 1,
      journal: 'clear',
      ui_state: item.get_status,
      label: terminal[0],
      tone: terminal[1],
      actions: [],
      may_claim_applied: terminal[2]
    }
  }
  assert.equal(item.get_status, 'pending_confirmation')
  const labels = {
    proposal_version_conflict: '提案已更新，请重新确认',
    proposal_execution_conflict: '计划正在更新，请重新确认',
    proposal_idempotency_conflict: '操作标识冲突，请重新确认'
  }
  return {
    ...shared,
    get_attempts: 1,
    journal: 'clear',
    ui_state: 'pending_confirmation',
    label: labels[item.error_code],
    tone: 'attention',
    actions: ['start_new_decision']
  }
}

test('UI integration fixture is strict, bounded, and uniquely identified', () => {
  assert.equal(fixture.fixture_version, '1.0.0')
  assert.deepEqual(fixture.scope, {
    surface: 'dedicated_proposal_detail_page',
    actions: ['confirm', 'reject'],
    mutation_endpoint_count: 2,
    ui_owns_authority: false
  })
  const groups = [
    [fixture.confirmation_cases, 7, CONFIRMATION_KEYS, CONFIRMATION_EXPECTED_KEYS],
    [fixture.button_lock_cases, 6, LOCK_KEYS, LOCK_EXPECTED_KEYS],
    [fixture.uncertain_result_cases, 5, UNCERTAIN_KEYS, UNCERTAIN_EXPECTED_KEYS],
    [fixture.manual_retry_cases, 5, RETRY_KEYS, RETRY_EXPECTED_KEYS],
    [fixture.error_presentation_cases, 14, ERROR_KEYS, ERROR_EXPECTED_KEYS]
  ]
  const identifiers = []
  for (const [cases, count, keys, expectedKeys] of groups) {
    assert.equal(cases.length, count)
    for (const item of cases) {
      assertSameKeys(item, keys)
      assertSameKeys(item.expected, expectedKeys)
      identifiers.push(item.case_id)
    }
  }
  assert.equal(new Set(identifiers).size, identifiers.length)
})

test('runtime UI projectors satisfy every fixed integration case', () => {
  const groups = [
    [fixture.confirmation_cases, runtime.projectProposalConfirmation],
    [fixture.button_lock_cases, runtime.projectProposalButtonLock],
    [fixture.uncertain_result_cases, runtime.projectProposalUncertainResult],
    [fixture.manual_retry_cases, runtime.projectProposalManualRetry],
    [fixture.error_presentation_cases, runtime.projectProposalErrorPresentation]
  ]
  for (const [cases, projector] of groups) {
    for (const item of cases) {
      assert.deepEqual(projector(item), item.expected, item.case_id)
    }
  }
})

test('sparse proposal summaries omit legacy null fields', () => {
  const values = displayRuntime.proposalChangeValues({
    change_type: 'update_plan_schedule',
    stable_display_key: 'plan-schedule',
    before: { duration_weeks: 6, days_per_week: null },
    after: { duration_weeks: 8, days_per_week: null },
    reason: '延长计划周期。',
    safety_priority: false
  })

  assert.deepEqual(values, {
    before: '计划周数：6 周',
    after: '计划周数：8 周'
  })
  assert.doesNotMatch(JSON.stringify(values), /null|undefined/)
})

test('proposal summaries render an explicit frequency change', () => {
  const values = displayRuntime.proposalChangeValues({
    change_type: 'update_plan_schedule',
    stable_display_key: 'plan-schedule',
    before: { days_per_week: 4 },
    after: { days_per_week: 3 },
    reason: '按个人训练频率重排计划。',
    safety_priority: false
  })

  assert.deepEqual(values, {
    before: '每周天数：4 天',
    after: '每周天数：3 天'
  })
})

test('fresh confirm submits once after review while risky or retry actions reconfirm', () => {
  for (const item of fixture.confirmation_cases) {
    assert.deepEqual(projectConfirmation(item), item.expected, item.case_id)
  }
  const posted = fixture.confirmation_cases.filter(
    item => item.expected.post_attempts > 0
  )
  assert.deepEqual(new Set(posted.map(item => item.action)), new Set([
    'confirm',
    'reject'
  ]))
  assert.ok(posted.every(item => item.detail_reviewed && item.user_choice === 'accept'))
  const freshConfirm = posted.find(item => (
    item.action === 'confirm' && item.existing_journal === 'none'
  ))
  assert.equal(freshConfirm.expected.modal, 'not_required')
  assert.ok(posted.filter(item => item.confirmation_required).every(item => (
    item.expected.modal === 'confirm_plan_adjustment' ||
    item.expected.modal === 'confirm_rejection'
  )))
})

test('one proposal lock blocks double taps, opposite actions, and refresh races', () => {
  for (const item of fixture.button_lock_cases) {
    assert.deepEqual(projectLock(item), item.expected, item.case_id)
    assert.equal(item.expected.new_post_attempts, 0, item.case_id)
  }
  const blocked = fixture.button_lock_cases.filter(
    item => item.completion === 'none'
  )
  assert.ok(blocked.every(item => item.expected.ignored_gestures === 1))
})

test('uncertain POST results query status and never automatically repost', () => {
  for (const item of fixture.uncertain_result_cases) {
    assert.deepEqual(projectUncertain(item), item.expected, item.case_id)
    assert.equal(item.expected.automatic_post_attempts, 0, item.case_id)
    assert.equal(item.expected.get_attempts, 1, item.case_id)
  }
  const applied = fixture.uncertain_result_cases.filter(
    item => item.expected.may_claim_applied
  )
  assert.deepEqual(applied.map(item => item.get_result), ['applied'])
})

test('manual retry is explicit, reconfirmed, and reuses the durable request id', () => {
  for (const item of fixture.manual_retry_cases) {
    assert.deepEqual(projectManualRetry(item), item.expected, item.case_id)
    assert.equal(item.expected.new_request_id_count, 0, item.case_id)
  }
  const posted = fixture.manual_retry_cases.filter(
    item => item.expected.post_attempts === 1
  )
  assert.ok(posted.every(item => (
    item.expected.modal_required && item.expected.request_id === 'reuse'
  )))
})

test('concurrency, expiry, and stale errors render only after authoritative recovery', () => {
  for (const item of fixture.error_presentation_cases) {
    assert.deepEqual(
      projectErrorPresentation(item),
      item.expected,
      item.case_id
    )
    assert.equal(item.expected.automatic_post_attempts, 0, item.case_id)
  }
  const coveredCodes = new Set(fixture.error_presentation_cases.map(
    item => item.error_code
  ))
  assert.deepEqual(coveredCodes, new Set([
    'proposal_not_found',
    'proposal_feature_disabled',
    'proposal_version_conflict',
    'proposal_not_pending',
    'proposal_expired',
    'proposal_base_plan_changed',
    'proposal_health_context_changed',
    'proposal_payload_invalid',
    'proposal_candidate_unavailable',
    'proposal_execution_failed',
    'proposal_execution_conflict',
    'proposal_idempotency_conflict',
    'proposal_client_contract_invalid'
  ]))
  const expired = fixture.error_presentation_cases.find(
    item => item.error_code === 'proposal_expired'
  )
  assert.equal(expired.expected.ui_state, 'expired')
  assert.equal(expired.expected.get_attempts, 1)
  assert.deepEqual(expired.expected.actions, [])
})

test('UI safety invariants prohibit optimistic or automatic mutation behavior', () => {
  assert.deepEqual(fixture.safety_invariants, {
    fresh_confirm_requires_second_confirmation: false,
    reject_requires_second_confirmation: true,
    full_detail_review_required_before_decision: true,
    single_proposal_mutation_lock: true,
    opposite_action_blocked_while_locked: true,
    refresh_blocked_while_mutation_in_flight: true,
    uncertain_result_forces_status_query: true,
    automatic_decision_repost_allowed: false,
    manual_retry_requires_second_confirmation: true,
    manual_retry_reuses_client_request_id: true,
    terminal_state_requires_server_evidence: true,
    client_clock_cannot_claim_expired: true,
    applied_state_cannot_be_optimistic: true
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
