import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred, runtime } from './helpers/page-runtime.mjs'

const previewFor = (days = [1, 3, 5], duration = 45) => ({
  name: '训练计划', goal: 'strength', duration_weeks: 4, days_per_week: days.length,
  session_duration_min: duration, rationale: [], safety_notes: [], exercise_options: [],
  exercises: days.map(day => ({ exercise_id: `exercise-${day}`, exercise_name: '划船',
    category: '力量', day_of_week: day, sets: 3, reps: '8-12', rest_seconds: 90, order_index: 0 }))
})
async function pageFor (overrides = {}) {
  const previews = [], writes = [], starts = [], hooks = {}
  const page = runtime('../../src/pages/plan-builder/index.tsx', {
    '../../components/ExercisePicker': { __esModule: true, default: () => null },
    '../../components/CustomExerciseEntry': { __esModule: true, default: () => null },
    '../../core/request': { errorMessage: e => e.message },
    '../../services/profile': { profileApi: { get: async () => ({ onboarding_completed: true, training_days_per_week: 3, session_duration_min: 45 }) } },
    '../../services/workouts': { workoutApi: {
      previewPersonalizedPlan: async body => { previews.push(body); return overrides.preview ? overrides.preview(body, previews.length) : previewFor(body.training_days, body.session_duration_min) },
      confirmPersonalizedPlan: async body => { writes.push(body); return overrides.confirm ? overrides.confirm(body) : { ...body, id: 'new-plan' } },
      start: async (...args) => { starts.push(args) }
    } },
    '@tarojs/taro': { __esModule: true, useLoad: fn => { hooks.load = fn }, default: {
      showToast: async () => {}, reLaunch: async () => {}, redirectTo: async () => {},
      showModal: async () => ({ confirm: true })
    } }
  })
  page.render(); hooks.load(); await page.flush()
  return { ...page, previews, writes, starts }
}
async function toggle (page, day) {
  const option = page.findAll('weekday')[day - 1]
  assert.ok(option, `Missing weekday ${day}`)
  option.props.onClick(); await page.flush()
}
async function selectWeekend (page) {
  for (const day of [1, 3, 5, 6, 7]) await toggle(page, day)
}

test('initial preview selects actual weekdays; seven options replace day-count slider', async () => {
  const page = await pageFor()
  assert.equal(page.findAll('weekday').length, 7)
  assert.deepEqual(page.findAll('selected').map(x => x.props.children), ['周一', '周三', '周五'])
  assert.equal(page.previews.length, 1)
  assert.equal(page.writes.length, 0)
})

test('selected weekend derives count, blocks stale save, then previews and saves exact weekdays', async () => {
  const page = await pageFor()
  await selectWeekend(page)
  assert.match(page.text(), /每周.*2/)
  assert.equal(page.find('save-button').props.disabled, true)
  await page.click('save-button'); assert.equal(page.writes.length, 0)
  await page.click('regenerate-button'); await page.flush()
  assert.deepEqual(page.previews.at(-1).training_days, [6, 7])
  assert.equal(page.previews.at(-1).days_per_week, 2)
  assert.deepEqual(page.findAll('day-title').map(x => x.props.children.join('')), ['周六训练', '周日训练'])
  await page.click('save-button'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.deepEqual(page.writes[0].training_days, [6, 7])
  assert.deepEqual(page.writes[0].exercises.map(x => x.day_of_week), [6, 7])
  assert.equal(page.starts.length, 0)
})

test('empty selection cannot generate or save, and existing preview edits survive', async () => {
  const page = await pageFor()
  page.input('reps-input', '6'); await page.flush()
  for (const day of [1, 3, 5]) await toggle(page, day)
  assert.equal(page.find('regenerate-button').props.disabled, true)
  await page.click('regenerate-button'); await page.click('save-button'); await page.flush()
  assert.equal(page.previews.length, 1); assert.equal(page.writes.length, 0)
  assert.equal(page.find('reps-input').props.value, '6')
})

test('failed regeneration keeps selected days and old draft; retry is explicit', async () => {
  const page = await pageFor({ preview: (body, call) => {
    if (call === 2) throw new Error('网络暂不可用')
    return previewFor(body.training_days, body.session_duration_min)
  } })
  page.input('reps-input', '6'); await page.flush(); await selectWeekend(page)
  await page.click('regenerate-button'); await page.flush()
  assert.deepEqual(page.findAll('selected').map(x => x.props.children), ['周六', '周日'])
  assert.equal(page.find('reps-input').props.value, '6')
  assert.equal(page.find('save-button').props.disabled, true)
  await page.click('regenerate-button'); await page.flush()
  assert.equal(page.previews.length, 3)
  assert.equal(page.find('save-button').props.disabled, false)
})

test('double generation and save cannot overlap; pending inputs cannot change the request', async () => {
  const pending = deferred()
  const page = await pageFor({ preview: (body, call) => call === 1 ? previewFor() : pending.promise })
  await selectWeekend(page)
  const staleSaveHandler = page.find('save-button').props.onClick
  const first = page.click('regenerate-button'), duplicate = page.click('regenerate-button')
  await staleSaveHandler(); await toggle(page, 2)
  assert.equal(page.previews.length, 2); assert.equal(page.writes.length, 0)
  pending.resolve(previewFor([6, 7])); await Promise.all([first, duplicate]); await page.flush()
  assert.deepEqual(page.findAll('selected').map(x => x.props.children), ['周六', '周日'])
})

test('legacy or malformed preview ignoring selected weekdays cannot replace the draft', async () => {
  const page = await pageFor({ preview: () => previewFor() })
  await selectWeekend(page); await page.click('regenerate-button'); await page.flush()
  assert.ok(page.find('error-banner'))
  assert.equal(page.find('save-button').props.disabled, true)
  assert.deepEqual(page.findAll('selected').map(x => x.props.children), ['周六', '周日'])
})

test('saving has a synchronous duplicate guard and preserves original weekdays', async () => {
  const pending = deferred()
  const page = await pageFor({ confirm: () => pending.promise })
  const first = page.click('save-button'), duplicate = page.click('save-button')
  await toggle(page, 7)
  assert.equal(page.writes.length, 1)
  pending.resolve({ ...previewFor(), id: 'saved' })
  await Promise.all([first, duplicate]); await page.flush()
  assert.deepEqual(page.writes[0].training_days, [1, 3, 5])
})

test('API transport includes explicit training days in the confirmation candidate', async () => {
  const calls = []
  const client = runtime('../../src/services/workouts.ts', { '../core/request': { apiRequest: async (...args) => calls.push(args) } })
  await client.exports.workoutApi.confirmPersonalizedPlan({ ...previewFor([6, 7]), training_days: [6, 7] })
  assert.deepEqual(calls[0][1].data.training_days, [6, 7])
})

test('same frequency but different weekdays still requires regeneration', async () => {
  const page = await pageFor()
  await toggle(page, 1); await toggle(page, 7)
  assert.match(page.text(), /每周.*3/)
  assert.equal(page.find('save-button').props.disabled, true)
  await page.click('regenerate-button'); await page.flush()
  assert.deepEqual(page.previews.at(-1).training_days, [3, 5, 7])
  assert.equal(page.find('save-button').props.disabled, false)
})

test('duration change cannot save old prescription, and Sunday-only/all-week schedules work', async () => {
  const page = await pageFor()
  page.find('session-duration-slider').props.onChange({ detail: { value: 60 } }); await page.flush()
  assert.equal(page.find('save-button').props.disabled, true)
  for (const day of [1, 3, 5, 7]) await toggle(page, day)
  await page.click('regenerate-button'); await page.flush()
  assert.deepEqual(page.previews.at(-1).training_days, [7])
  assert.equal(page.previews.at(-1).session_duration_min, 60)
  for (const day of [1, 2, 3, 4, 5, 6]) await toggle(page, day)
  await page.click('regenerate-button'); await page.flush()
  assert.equal(page.findAll('day-title').length, 7)
  assert.equal(page.previews.at(-1).days_per_week, 7)
})

test('save-and-start uses a selected weekday and failed save retains selection for retry', async () => {
  let fail = true
  const page = await pageFor({ confirm: async body => {
    if (fail) throw new Error('保存失败')
    return { ...body, id: 'saved-plan' }
  } })
  await selectWeekend(page); await page.click('regenerate-button'); await page.flush()
  await page.click('start-now-button'); await page.flush()
  assert.equal(page.starts.length, 0)
  assert.deepEqual(page.findAll('selected').map(x => x.props.children), ['周六', '周日'])
  fail = false; await page.click('start-now-button'); await page.flush()
  assert.equal(page.starts.length, 1)
  assert.equal(page.starts[0][0], 'saved-plan')
  assert.ok([6, 7].includes(page.starts[0][1]))
})
