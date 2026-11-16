import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import ts from 'typescript'


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

const runtime = loadTypeScriptModule('../src/core/proposal-reference.ts')

const fingerprint = 'a'.repeat(64)
const pendingReference = {
  id: 'proposal-1',
  proposal_type: 'plan_adjustment_v1',
  status: 'pending_confirmation',
  version: 1,
  expires_at: '2026-08-28T09:31:00Z',
  payload_fingerprint: fingerprint
}

test('historical proposal references retain authoritative terminal states', () => {
  for (const status of [
    'pending_confirmation',
    'applied',
    'rejected',
    'expired',
    'stale',
    'failed'
  ]) {
    assert.deepEqual(
      runtime.proposalReferenceFromUnknown({ ...pendingReference, status }),
      { ...pendingReference, status },
      status
    )
  }
})

test('agent proposal references accept every supported domain type', () => {
  for (const proposalType of [
    'plan_adjustment_v1',
    'plan_creation_v1',
    'plan_adjustment_v2',
    'plan_deletion_v1',
    'profile_update_v1',
    'weight_log_create_v1',
    'meal_log_create_v1',
    'daily_meal_log_create_v1',
    'meal_log_delete_v1'
  ]) {
    const value = { ...pendingReference, proposal_type: proposalType }
    assert.deepEqual(runtime.proposalReferenceFromUnknown(value), value)
  }
})

test('proposal reads replace the message snapshot with server authority', () => {
  const authoritative = runtime.proposalReferenceFromRead({
    ...pendingReference,
    status: 'applied',
    version: 2,
    created_at: '2026-08-27T09:31:00Z',
    updated_at: '2026-08-27T09:35:00Z',
    allowed_actions: [],
    payload: {},
    result: {
      plan_id: 'plan-1',
      plan_fingerprint: 'b'.repeat(64),
      applied_at: '2026-08-27T09:35:00Z'
    }
  })

  assert.deepEqual(authoritative, {
    ...pendingReference,
    status: 'applied',
    version: 2
  })
})

test('not-found authority is explicit and preserves traceable reference data', () => {
  assert.deepEqual(runtime.unavailableProposalReference(pendingReference), {
    ...pendingReference,
    status: 'missing'
  })
})

test('agent page refreshes proposal authority on restore and page return', () => {
  const source = readFileSync(
    new URL('../src/pages/agent/index.tsx', import.meta.url),
    'utf8'
  )
  assert.match(source, /useDidShow\(\(\) => \{/)
  assert.match(source, /proposalsApi\.get\(id\)/)
  assert.match(source, /synchronizeProposalReferences\(restored\)/)
  assert.match(source, /proposalSyncQueued\.current = true/)
  assert.match(source, /presentation\.terminal \? '查看结果 →' : '查看详情 →'/)
})

test('daily meal artifact card stays review-only until save is requested', () => {
  const source = readFileSync(
    new URL('../src/pages/agent/index.tsx', import.meta.url),
    'utf8'
  )
  assert.match(source, /card\.type === 'daily_meal_plan'/)
  assert.match(source, /本次参考：/)
  assert.match(source, /acceptable_deviation/)
  assert.match(source, /接近目标/)
  assert.match(source, /理想范围/)
  assert.match(source, /保存为待确认提案/)
  assert.match(source, /action: 'save_as_proposal'/)
  assert.match(source, /artifact_id: artifact\.id/)
  assert.match(source, /expected_version: artifact\.version/)
  assert.match(source, /onAction\('保存这份方案', artifactAction\)/)

  const serviceSource = readFileSync(
    new URL('../src/services/agent.ts', import.meta.url),
    'utf8'
  )
  assert.match(serviceSource, /artifact_action: artifactAction/)
})

test('multi-meal proposal detail renders per-meal items and nutrition totals', () => {
  const source = readFileSync(
    new URL('../src/pages/domain-proposal-detail/index.tsx', import.meta.url),
    'utf8'
  )
  assert.match(source, /daily_meal_log_create_v1/)
  assert.match(source, /value\.meals/)
  assert.match(source, /全天合计/)
  assert.match(source, /NutritionFit/)
  assert.match(source, /接近目标，请核对偏差/)
})

test('fresh confirm bypasses modal while reject and uncertain retry reconfirm', () => {
  const source = readFileSync(
    new URL('../src/pages/proposal-detail/index.tsx', import.meta.url),
    'utf8'
  )
  assert.match(source, /confirmationRequired = action === 'reject' \|\| retry/)
  assert.match(source, /confirmation_required: confirmationRequired/)
  assert.match(source, /if \(confirmationRequired && !confirmationGranted\)/)
})
