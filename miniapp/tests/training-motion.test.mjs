import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred } from './helpers/page-runtime.mjs'
import { interactionRuntime, manualClock } from './helpers/interaction-runtime.mjs'

const exercise = { id: 'sx', exercise_id: 'e', exercise_name: '测试卧推', target_sets: 2, target_reps: '8', rest_seconds: 90, sets_data: [], previous_sets_data: [] }
const blank = { id: 's', status: 'in_progress', started_at: '2026-09-10T01:00:00Z', total_sets: 0, total_volume_kg: 0, exercises: [exercise] }
async function setup ({ storedPreference = false, setGate, failRest = false, lostSet = false, personalRecord = false, initialSession = blank } = {}) {
  const hooks = {}, storage = new Map([['fitness_reduce_motion_v1', storedPreference]]), calls = { sets: [], rests: [], reads: 0, planReads: 0, toasts: [], completes: [], earlyEnds: [], navigations: [], preferenceReads: 0 }, clock = manualClock()
  let current = structuredClone(initialSession), shouldFailRest = failRest
  const api = {
    active: async () => { calls.reads++; return current }, plans: async () => { calls.planReads++; return [] },
    recordSet: async (...args) => {
      calls.sets.push(args)
      const [, , number, reps, weight, started] = args
      if (setGate) await setGate.promise
      current = { ...current, total_sets: 1, exercises: [{ ...exercise, sets_data: [{ set_number: number, reps, weight_kg: weight, rest_started_at: started, is_personal_record: personalRecord }] }] }
      if (lostSet) throw new Error('响应丢失')
      return current
    },
    recordRest: async (...args) => {
      calls.rests.push(args)
      if (shouldFailRest) throw new Error('断网')
      return current
    },
    complete: async (...args) => { calls.completes.push(args); current = { ...current, status: 'completed', total_reps: 8, adjustments: [] }; return current },
    finishEarly: async (...args) => { calls.earlyEnds.push(args); throw new Error('The removed action must not be invoked') }
  }
  const page = interactionRuntime('../../src/pages/workout-active/index.tsx', {
    '@tarojs/taro': { __esModule: true,
      useLoad: fn => { hooks.load = fn }, useDidShow: fn => { hooks.show = fn }, useDidHide: fn => { hooks.hide = fn },
      default: { getStorageSync: key => { if (key === 'fitness_reduce_motion_v1') calls.preferenceReads++; return storage.get(key) }, setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
        showToast: async options => { calls.toasts.push(options) }, navigateBack: async () => {}, redirectTo: async options => { calls.navigations.push(options) } } },
    '../../core/request': { errorMessage: e => e.message }, '../../services/workouts': { workoutApi: api }
  }, clock.globals)
  page.render(); hooks.load({}); await page.flush()
  return { ...page, hooks, clock, calls, storage, restSucceeds: () => { shouldFailRest = false } }
}

test('real save controls feedback and a pending request cannot be submitted twice', async () => {
  const gate = deferred(), page = await setup({ setGate: gate })
  const first = page.click('set-save'), second = page.click('set-save')
  await page.flush()
  assert.equal(page.calls.sets.length, 1)
  assert.match(page.text(), /保存中/)
  assert.equal(page.find('is-just-saved'), undefined)
  assert.equal(page.find('rest-bar'), undefined)
  gate.resolve(); await Promise.all([first, second]); await page.flush()
  assert.ok(page.find('is-just-saved')); assert.ok(page.find('rest-entering'))
  assert.match(page.text(), /已保存/)
  assert.equal(page.calls.sets[0][5], new Date(100000).toISOString())
  page.clock.advance(650); await page.flush()
  assert.equal(page.find('is-just-saved'), undefined); assert.ok(page.find('is-saved'))
  assert.equal(page.calls.reads, 1); assert.equal(page.calls.planReads, 1)
  assert.equal(page.calls.sets.length, 1); page.unmount(); assert.equal(page.clock.count(), 0)
})

test('editing while save resolves preserves the newer input without claiming it was saved', async () => {
  const gate = deferred(), page = await setup({ setGate: gate })
  const saving = page.click('set-save'); await page.flush()
  page.input('set-input', '32'); await page.flush()
  gate.resolve(); await saving; await page.flush()
  assert.equal(page.find('set-input').props.value, '32')
  assert.equal(page.find('is-just-saved'), undefined)
  assert.equal(page.find('is-saved'), undefined)
  page.unmount()
})

test('failed or lost save never animates success, reload restores rest without replay', async () => {
  const page = await setup({ lostSet: true })
  await page.click('set-save'); await page.flush()
  assert.equal(page.find('is-just-saved'), undefined); assert.equal(page.find('rest-entering'), undefined)
  assert.equal(page.calls.sets.length, 1)
  await page.click('reload-workout'); await page.flush()
  assert.ok(page.find('rest-bar')); assert.equal(page.find('rest-entering'), undefined)
  assert.equal(page.calls.sets.length, 1); page.unmount()
})

test('hide cancels decorative timers; late success stays real but never replays on return', async () => {
  const gate = deferred(), page = await setup({ setGate: gate, personalRecord: true })
  const saving = page.click('set-save'); await page.flush()
  page.hooks.hide(); await page.flush(); assert.equal(page.clock.count(), 0)
  page.clock.advance(17000); gate.resolve(); await saving; await page.flush()
  assert.equal(page.find('rest-entering'), undefined); assert.equal(page.find('is-just-saved'), undefined)
  assert.equal(page.clock.count(), 0); assert.equal(page.calls.toasts.length, 0)
  page.hooks.show(); await page.flush()
  assert.match(page.text(), /组间休息 1:13/); assert.equal(page.find('rest-entering'), undefined)
  assert.equal(page.calls.reads, 1); assert.equal(page.calls.planReads, 1)
  assert.equal(page.calls.sets.length, 1); page.unmount(); assert.equal(page.clock.count(), 0)
})

test('visible records retain their real success notice but old receipts cannot replay it after a hide/show cycle', async () => {
  const current = await setup({ personalRecord: true })
  await current.click('set-save'); await current.flush()
  assert.equal(current.calls.toasts.length, 1)
  assert.match(current.calls.toasts[0].title, /新个人纪录/)
  current.unmount()
  const gate = deferred(), late = await setup({ setGate: gate, personalRecord: true })
  const saving = late.click('set-save'); await late.flush()
  late.hooks.hide(); await late.flush(); late.hooks.show(); await late.flush()
  gate.resolve(); await saving; await late.flush()
  assert.equal(late.calls.toasts.length, 0)
  assert.equal(late.find('is-just-saved'), undefined)
  assert.equal(late.calls.sets.length, 1)
  late.unmount(); assert.equal(late.clock.count(), 0)
})

for (const storedPreference of [false, true]) {
  test(`rest ending keeps its time and normal feedback regardless of removed legacy preference (${storedPreference})`, async () => {
    const page = await setup({ storedPreference, failRest: true })
    await page.click('set-save'); await page.flush()
    assert.ok(page.find('rest-entering'))
    assert.equal(page.calls.preferenceReads, 0)
    page.clock.advance(17000); await page.click('end-rest'); await page.flush()
    assert.match(page.text(), /重试保存休息/)
    assert.equal(page.find('rest-exit-feedback'), undefined)
    page.clock.advance(30000); page.restSucceeds(); await page.click('end-rest'); await page.flush()
    assert.deepEqual(page.calls.rests[0], page.calls.rests[1])
    assert.equal(page.calls.rests[0][3].actual_rest_seconds, 17)
    assert.equal(page.find('rest-bar'), undefined); assert.equal(page.find('end-rest'), undefined)
    assert.ok(page.find('rest-exit-feedback'))
    page.clock.advance(250); await page.flush(); assert.equal(page.find('rest-exit-feedback'), undefined)
    page.unmount(); assert.equal(page.clock.count(), 0)
  })
}

test('hiding during feedback stops decoration and returning restores only the real clock', async () => {
  const page = await setup({ storedPreference: true })
  await page.click('set-save'); await page.flush(); assert.ok(page.find('rest-entering'))
  page.hooks.hide(); await page.flush()
  assert.ok(page.find('micro-motion-static')); assert.equal(page.find('rest-entering'), undefined)
  assert.equal(page.clock.count(), 0)
  page.clock.advance(17000); page.hooks.show(); await page.flush()
  assert.ok(page.find('micro-motion-enabled')); assert.equal(page.find('rest-entering'), undefined)
  assert.match(page.text(), /组间休息 1:13/)
  assert.equal(page.calls.sets.length, 1); assert.equal(page.calls.rests.length, 0)
  page.hooks.hide(); await page.flush(); assert.equal(page.clock.count(), 0)
  page.unmount()
})

test('stable button host guards busy clicks and leaves touch cancellation to the native button', async () => {
  let clicks = 0, cancels = 0
  const page = interactionRuntime('../../src/components/FeedbackButton.tsx')
  const props = { className: 'test-button', busy: true, children: '保存', onClick: () => clicks++, onTouchCancel: () => cancels++ }
  page.render(props)
  assert.equal(page.find('test-button').type, 'Button')
  assert.equal(page.find('test-button').props.disabled, true)
  assert.equal(page.find('test-button').props.hoverClass, 'none')
  page.click('test-button'); page.invoke('test-button', 'onTouchCancel', {})
  assert.equal(clicks, 0); assert.equal(cancels, 1)
  page.render({ ...props, busy: false })
  assert.equal(page.find('test-button').props.hoverStartTime, 0)
  page.click('test-button'); assert.equal(clicks, 1); page.unmount()
})

for (const offline of [false, true]) {
  test(`profile does not expose or access the removed motion setting (offline=${offline})`, async () => {
    const hooks = {}; let reads = 0, storageReads = 0, storageWrites = 0
    const page = interactionRuntime('../../src/pages/me/index.tsx', {
      '@tarojs/taro': { __esModule: true, useDidShow: fn => { hooks.show = fn }, useDidHide: fn => { hooks.hide = fn }, default: {
        getStorageSync: () => { storageReads++; return true }, setStorageSync: () => { storageWrites++ } } },
      '../../core/request': { errorMessage: e => e.message },
      '../../services/profile': { profileApi: { get: async () => { reads++; if (offline) throw new Error('断网'); return { injuries: [], chronic_conditions: [], weight_kg: 70 } }, weightHistory: async () => [] } }
    })
    page.render(); hooks.show(); await page.flush()
    assert.equal(page.find('motion-preference'), undefined)
    assert.equal(page.find('reduce-motion-switch'), undefined)
    assert.doesNotMatch(page.text(), /减少动态效果/)
    assert.equal(storageReads, 0); assert.equal(storageWrites, 0); assert.equal(reads, 1)
    if (offline) assert.match(page.text(), /断网/)
    else { assert.ok(page.find('profile-summary')); assert.match(page.text(), /个人档案/) }
    page.unmount()
  })
}

for (const orphaned of [false, true]) {
  test(`active training omits the redundant early-end entry without any mutation (orphaned=${orphaned})`, async () => {
    const page = await setup({ initialSession: { ...blank, orphaned } })
    assert.equal(page.find('open-early-end'), undefined)
    assert.equal(page.find('confirm-early-end'), undefined)
    assert.equal(page.find('early-end-card'), undefined)
    assert.doesNotMatch(page.text(), /保留记录并提前结束|可在下方保留记录并结束/)
    assert.ok(page.find('complete-button')); assert.ok(page.find('abandon-link'))
    assert.equal(page.calls.earlyEnds.length, 0); assert.equal(page.calls.completes.length, 0)
    if (orphaned) {
      assert.match(page.text(), /完成训练/)
      assert.doesNotMatch(page.text(), /返回训练列表处理旧训练/)
    }
    page.unmount()
  })
}

for (const orphaned of [false, true]) {
  test(`completion stays an explicit feedback submission, even without its old plan (orphaned=${orphaned})`, async () => {
    const page = await setup({ initialSession: { ...blank, orphaned } })
    await page.click('set-save'); await page.flush()
    await page.click('end-rest'); await page.flush()
    await page.click('complete-button'); await page.flush()
    assert.ok(page.find('feedback-sheet')); assert.equal(page.calls.completes.length, 0)
    const actions = page.find('feedback-actions')
    await actions.props.children[1].props.onClick({}); await page.flush()
    assert.equal(page.calls.completes.length, 1); assert.equal(page.calls.earlyEnds.length, 0)
    assert.equal(page.calls.completes[0][1].difficulty_feedback, 'just_right')
    assert.ok(page.find('completion-page')); page.unmount()
  })
}

test('an empty old workout is not silently completed or discarded after removing its shortcut', async () => {
  const page = await setup({ initialSession: { ...blank, orphaned: true } })
  assert.equal(page.find('complete-button').props.disabled, true)
  await page.click('complete-button'); await page.flush()
  assert.equal(page.find('feedback-sheet'), undefined)
  assert.ok(page.find('abandon-link')); assert.match(page.text(), /没有记录时，可点击“放弃”退出/)
  assert.equal(page.calls.completes.length, 0); assert.equal(page.calls.earlyEnds.length, 0)
  page.unmount()
})

test('previously early-ended records still show their preserved result and detail navigation', async () => {
  const page = await setup({ initialSession: { ...blank, status: 'ended_early', total_sets: 1 } })
  assert.equal(page.find('early-end-card').props.children.join(''), '已提前结束，1 组训练记录已保留。')
  await page.click('ended-detail')
  assert.deepEqual(page.calls.navigations, [{ url: '/pages/workout-detail/index?id=s' }])
  assert.equal(page.calls.earlyEnds.length, 0); assert.equal(page.calls.completes.length, 0)
  page.unmount()
})
