import assert from 'node:assert/strict'
import test from 'node:test'
import { interactionRuntime } from './helpers/interaction-runtime.mjs'

const base = { name: '计划', duration_weeks: 4, training_days: [2], exercises: [{ item_key: 'a', exercise_id: 'row', exercise_name: '划船', category: '力量', day_of_week: 2, sets: 3, reps: '8', rest_seconds: 90, recommended_weight_kg: null, order_index: 0 }] }
async function fixture (api = {}) {
  const calls = [], hooks = {}, writes = []
  const page = interactionRuntime('../../src/pages/plan-editor/index.tsx', {
    '../../components/ExercisePicker': { __esModule: true, default: () => null },
    '../../components/CustomExerciseEntry': { __esModule: true, default: props => ({ type: 'CustomEntry', props: { ...props, className: 'custom-entry-test' } }) },
    '../../core/request': { errorMessage: e => e.message },
    '../../services/plan-management': { planManagementApi: {
      editContext: async () => ({ base_plan: structuredClone(base), base_plan_fingerprint: 'base-v1', exercise_options: [], proposals_enabled: true }),
      createAdjustment: async (...args) => { writes.push(args); return { id: 'proposal' } }, ...api
    } },
    '@tarojs/taro': { __esModule: true,
      useLoad: fn => { hooks.load = fn }, useDidShow: fn => { hooks.show = fn }, useDidHide: fn => { hooks.hide = fn },
      default: {
        enableAlertBeforeUnload: async () => { calls.push('enable') }, disableAlertBeforeUnload: async () => { calls.push('disable') },
        navigateTo: async () => { calls.push('navigate') }
      }
    }
  })
  page.render(); hooks.load({ id: 'plan' }); await page.flush(); await page.flush()
  return { ...page, calls, writes, hooks }
}

test('viewing an untouched plan does not enable the leave warning', async () => {
  const page = await fixture()
  assert.equal(page.calls.includes('enable'), false)
  assert.equal(page.writes.length, 0); page.unmount()
})

test('changing then restoring a plan field enables then removes the warning', async () => {
  const page = await fixture()
  const slider = page.find('plan-duration-slider')
  slider.props.onChange({ detail: { value: 5 } }); await page.flush(); await page.flush()
  assert.equal(page.calls.at(-1), 'enable')
  page.find('plan-duration-slider').props.onChange({ detail: { value: 4 } }); await page.flush(); await page.flush()
  assert.equal(page.calls.at(-1), 'disable'); page.unmount()
})

test('an empty sheet is not a dirty plan, but unsaved custom fields remain protected after closing', async () => {
  const page = await fixture(); const entry = page.find('custom-entry-test')
  entry.props.onOpenChange(true); await page.flush(); await page.flush()
  assert.equal(page.calls.includes('enable'), false)
  assert.match(page.find('plan-sheet-meta').props.pageStyle, /overflow: hidden/)
  page.find('custom-entry-test').props.onDraftChange(true); await page.flush(); await page.flush()
  assert.equal(page.calls.at(-1), 'enable')
  page.find('custom-entry-test').props.onOpenChange(false); await page.flush()
  assert.equal(page.find('plan-sheet-meta').props.pageStyle, '')
  page.find('custom-entry-test').props.onDraftChange(false); await page.flush(); await page.flush()
  assert.equal(page.calls.at(-1), 'disable'); page.unmount()
})

test('proposal failure keeps the dirty guard; successful save clears it without confirming a proposal', async () => {
  let attempt = 0
  const page = await fixture({ createAdjustment: async () => { if (++attempt === 1) throw new Error('网络失败'); return { id: 'p' } } })
  page.find('plan-duration-slider').props.onChange({ detail: { value: 5 } }); await page.flush(); await page.flush()
  await page.click('save-proposal'); await page.flush(); await page.flush()
  assert.equal(page.calls.at(-1), 'enable'); assert.match(page.text(), /网络失败/)
  await page.click('save-proposal'); await page.flush(); await page.flush()
  assert.equal(attempt, 2); assert.ok(page.calls.lastIndexOf('disable') < page.calls.lastIndexOf('navigate'))
  page.unmount()
})

test('draft equality ignores display metadata but protects invalid numeric edits', async () => {
  const page = await fixture()
  const { planDraftSignature } = page.loadSource('../../src/core/plan-editor-draft.ts')
  const before = planDraftSignature(4, [2], base.exercises)
  assert.equal(planDraftSignature(4, [2], base.exercises.map(row => ({ ...row, exercise_name: '刷新后的名称' }))), before)
  assert.notEqual(planDraftSignature(4, [2], base.exercises.map(row => ({ ...row, recommended_weight_kg: NaN }))), before)
  assert.notEqual(planDraftSignature(4, [2], base.exercises.map(row => ({ ...row, reps: '' }))), before)
  page.unmount()
})

test('hide suppresses pending native guard calls; showing resumes the actual dirty state', async () => {
  const page = await fixture()
  page.find('plan-duration-slider').props.onChange({ detail: { value: 5 } }); page.render()
  page.hooks.hide(); await page.flush(); await page.flush()
  assert.equal(page.calls.includes('enable'), false)
  page.hooks.show(); await page.flush(); await page.flush()
  assert.equal(page.calls.at(-1), 'enable'); page.unmount()
})
