import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'


const source = relativePath => readFileSync(new URL(relativePath, import.meta.url), 'utf8')

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
  new Function('exports', 'module', output)(loaded.exports, loaded)
  return loaded.exports
}

const schedule = loadTypeScriptModule('../src/core/workout-schedule.ts')

test('review warning exposes a direct and explained repair path', () => {
  const workouts = source('../src/pages/workouts/index.tsx')
  assert.match(workouts, /计划需复核/)
  assert.match(workouts, /修复计划/)
  assert.match(workouts, /当前环境未开启手动计划提案/)
  assert.match(workouts, /pages\/plan-editor\/index/)
})

test('first workout uses today or the nearest following planned weekday', () => {
  const builder = source('../src/pages/plan-builder/index.tsx')
  const runtime = source('../src/core/workout-schedule.ts')
  assert.match(builder, /nextTrainingDay/)
  assert.match(runtime, /candidateDistance < bestDistance/)
  assert.doesNotMatch(builder, /Math\.min\(\.\.\.plan\.exercises/)
  assert.match(builder, /confirmText: '开始训练'/)
  assert.match(builder, /cancelText: '仅保存'/)
  assert.equal(schedule.nextTrainingDay([1, 3, 6], new Date('2026-09-02T12:00:00')), 3)
  assert.equal(schedule.nextTrainingDay([1, 3, 6], new Date('2026-09-06T12:00:00')), 1)
  assert.equal(schedule.nextTrainingDay([2, 7], new Date('2026-09-06T12:00:00')), 7)
})

test('workout completion shows proposal state and never claims automatic adjustment', () => {
  const active = source('../src/pages/workout-active/index.tsx')
  const history = source('../src/pages/history/index.tsx')
  assert.doesNotMatch(active, /完成并调整下一练|下一练已自动调整/)
  assert.match(active, /查看调整提案/)
  assert.match(active, /pending_confirmation/)
  assert.match(history, /待确认/)
  assert.match(history, /已应用/)
  assert.match(history, /已拒绝/)
  assert.match(history, /已失效/)
})

test('meal editor submits a complete PUT candidate and supports item correction', () => {
  const service = source('../src/services/nutrition.ts')
  const page = source('../src/pages/nutrition/index.tsx')
  assert.match(service, /method: 'PUT'/)
  assert.match(page, /保存修改/)
  assert.match(page, /updateItemAmount/)
  assert.match(page, /updateCustomNutrition/)
  assert.match(page, /earliestEditableDate/)
})

test('agent history and structured clarification are reachable', () => {
  const config = source('../src/app.config.ts')
  const agent = source('../src/pages/agent/index.tsx')
  const conversations = source('../src/pages/agent-conversations/index.tsx')
  assert.match(config, /pages\/agent-conversations\/index/)
  assert.match(agent, /clarification_choices/)
  assert.match(agent, /select_plan_occurrences/)
  assert.match(conversations, /agentApi\.conversations\(50\)/)
  assert.match(conversations, /saveAgentConversationId/)
})
