import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime, deferred } from './helpers/page-runtime.mjs'
import { manualClock } from './helpers/interaction-runtime.mjs'

const stats = { weeks: 8, total_sessions: 1, total_sets: 1, total_reps: 8, total_volume_kg: 200, weekly: [{ week_start: '2026-09-07', sessions: 1, sets: 1, reps: 8, volume_kg: 200 }, { week_start: '2026-08-31', sessions: 0, sets: 0, reps: 0, volume_kg: 0 }] }
const exercise = { id: 'sx', exercise_id: 'e', exercise_name: '测试动作', day_of_week: 1, sets_data: [], previous_sets_data: [], target_sets: 2, target_reps: '8', rest_seconds: 90 }
const session = { id: 's', status: 'completed', trained_at: '2026-09-07', started_at: '2026-09-07T01:00:00Z', total_sets: 1, total_reps: 8, total_volume_kg: 200, exercises: [{ ...exercise, sets_data: [{ set_number: 1, reps: 8, weight_kg: 25 }] }], adjustments: [], adaptive_adjustment_status: 'not_needed' }
const plan = { id: 'p', name: '旧计划', display_name: '训练计划', is_active: true, safety_status: 'compatible', safety_reasons: [], manual_proposals_enabled: true, exercises: [exercise], weekly_completed_days: 1, weekly_sessions: [{ day_of_week: 1, session_id: 's', status: 'completed' }] }

function platform () {
  const hooks = {}, navigations = [], storage = new Map()
  return { hooks, navigations, storage, module: { __esModule: true,
    useDidShow: fn => { hooks.show = fn }, useLoad: fn => { hooks.load = fn }, useDidHide: fn => { hooks.hide = fn },
    default: { navigateTo: async value => { navigations.push(value) }, navigateBack: async () => {}, redirectTo: async value => { navigations.push(value) }, showToast: async () => {}, enableAlertBeforeUnload: () => {},
      getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
      showModal: () => { throw new Error('Unexpected modal') } }
  } }
}

for (const isActive of [true, false]) {
  test(`completed day in ${isActive ? 'active' : 'archived'} plan opens read-only session and cannot call start`, async () => {
    const p = platform(); let writes = 0
    const page = runtime('../../src/pages/workouts/index.tsx', {
      '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message },
      '../../services/profile': { profileApi: { get: async () => ({ onboarding_completed: true }) } },
      '../../services/plan-management': { planManagementApi: {} },
      '../../services/workouts': { workoutApi: { plans: async () => [{ ...plan, is_active: isActive }], active: async () => null, progress: async () => stats, start: async () => { writes++ } } }
    })
    page.render(); p.hooks.show(); await page.flush()
    if (isActive) {
      assert.match(page.text(), /本周计划已完成/)
      assert.equal(page.find('delete-archive'), undefined)
    } else assert.ok(page.find('archived-tag'))
    assert.equal(page.find('start-button').props.disabled, false)
    assert.equal(page.find('start-button').props.children, '已完成')
    assert.equal(page.find('start-button').props.ariaLabel, '已完成，查看周一训练详情')
    await page.click('start-button')
    assert.equal(writes, 0)
    assert.deepEqual(p.navigations, [{ url: '/pages/workout-detail/index?id=s' }])
  })
}

test('archive deletion still requires inline choice and preserves state on failure', async () => {
  const p = platform(); let removed = 0, failed = true
  const api = { plans: async () => removed ? [] : [{ ...plan, is_active: false }], active: async () => null, progress: async () => stats,
    removeArchived: async () => { if (failed) throw new Error('连接失败'); removed++ } }
  const page = runtime('../../src/pages/workouts/index.tsx', {
    '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message }, '../../services/profile': { profileApi: { get: async () => ({ onboarding_completed: true }) } },
    '../../services/plan-management': { planManagementApi: {} }, '../../services/workouts': { workoutApi: api }
  })
  page.render(); p.hooks.show(); await page.flush()
  const heading = page.find('plan-heading')
  const headingActions = heading.props.children.find(item => item?.props?.className === 'archive-header-actions')
  assert.ok(headingActions, 'archive delete must be next to the archived badge in the heading')
  const [deleteButton, archivedBadge] = headingActions.props.children
  assert.ok(deleteButton.props.className.split(' ').includes('delete-archive'))
  assert.equal(deleteButton.props.children.props.children, '删除')
  assert.equal(deleteButton.props.size, undefined, 'custom sizing must not inherit the native mini button preset')
  assert.ok(deleteButton.props.children.props.className.split(' ').includes('archive-status-chip'))
  assert.ok(archivedBadge.props.className.split(' ').includes('archive-status-chip'))
  assert.ok(archivedBadge.props.className.split(' ').includes('archived-tag'))
  assert.ok(!deleteButton.props.className.split(' ').includes('secondary-button'), 'compact delete must not inherit the generic large button')
  assert.equal(page.find('archive-confirm'), undefined)
  page.click('delete-archive'); await page.flush(); assert.equal(removed, 0)
  page.click('cancel-archive-delete'); await page.flush(); assert.equal(removed, 0); assert.equal(page.find('archive-confirm'), undefined)
  page.click('delete-archive'); await page.flush()
  await page.click('confirm-archive-delete'); await page.flush(); assert.ok(page.find('archive-confirm')); assert.equal(removed, 0)
  failed = false; await page.click('confirm-archive-delete'); await page.flush(); assert.equal(removed, 1)
})

for (const orphaned of [false, true]) {
  for (const totalSets of [0, 1]) {
    test(`home keeps only the continue card for active workouts (orphaned=${orphaned}, sets=${totalSets})`, async () => {
      const p = platform(); let writes = 0
      const api = {
        plans: async () => orphaned ? [] : [plan],
        active: async () => ({ ...session, status: 'in_progress', orphaned, total_sets: totalSets }), progress: async () => stats,
        finishEarly: async () => { writes++ }, complete: async () => { writes++ }, abandon: async () => { writes++ }
      }
      const page = runtime('../../src/pages/workouts/index.tsx', {
        '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message },
        '../../services/profile': { profileApi: { get: async () => ({ onboarding_completed: true }) } },
        '../../services/plan-management': { planManagementApi: {} }, '../../services/workouts': { workoutApi: api }
      })
      page.render(); p.hooks.show(); await page.flush()
      assert.equal(page.find('active-recovery'), undefined)
      assert.equal(page.find('show-end-active'), undefined); assert.equal(page.find('finish-active'), undefined)
      assert.doesNotMatch(page.text(), /保留记录并结束旧训练|保留记录并结束/)
      assert.ok(page.find('active-card')); assert.match(page.text(), /继续 →/)
      await page.click('active-card')
      assert.deepEqual(p.navigations, [{ url: '/pages/workout-active/index' }]); assert.equal(writes, 0)
      p.hooks.show(); await page.flush()
      assert.ok(page.find('active-card')); assert.equal(page.find('active-recovery'), undefined); assert.equal(writes, 0)
    })
  }
}

test('week selection changes seven bars, metrics and records; old late response cannot replace newer choice', async () => {
  const p = platform(), one = deferred(), two = deferred()
  const week = (start, total) => ({ ...stats, selected_week: start, total_sets: total, daily: Array.from({ length: 7 }, (_, i) => ({ date: `${start.slice(0, 8)}${String(Number(start.slice(-2)) + i).padStart(2, '0')}`, volume_kg: i * 10 })) })
  const page = runtime('../../src/pages/history/index.tsx', {
    '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message },
    '../../services/workouts': { workoutApi: { history: async () => [session], progress: async (_, start) => !start ? stats : start === '2026-09-07' ? one.promise : two.promise } }
  })
  page.render(); p.hooks.show(); await page.flush()
  const first = page.findAll('week-column')[0].props.onClick()
  const second = page.findAll('week-column')[1].props.onClick()
  two.resolve(week('2026-08-24', 3)); await second; await page.flush()
  one.resolve(week('2026-09-07', 99)); await first; await page.flush()
  assert.equal(page.findAll('day-column').length, 7)
  assert.equal(page.findAll('history-card').length, 0)
  assert.match(page.text(), /本周还没有训练记录/)
  page.click('show-all-weeks'); await page.flush(); assert.equal(page.findAll('history-card').length, 1)
  await page.click('workout-detail-link'); assert.deepEqual(p.navigations, [{ url: '/pages/workout-detail/index?id=s' }])
})

test('workout detail shows per-set actual and unknown rest without any writes', async () => {
  const p = platform(); let reads = 0
  const page = runtime('../../src/pages/workout-detail/index.tsx', {
    '../services/workouts': { workoutApi: {} }, '../core/request': { errorMessage: e => e.message },
    '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message },
    '../../services/workouts': { workoutApi: { detail: async id => { reads++; assert.equal(id, 's'); return { ...session, exercises: [{ ...exercise, sets_data: [{ reps: 8, weight_kg: 25, actual_rest_seconds: 17 }, { reps: 7, weight_kg: 22.5 }] }] } } } }
  })
  page.render(); p.hooks.load({ id: 's' }); await page.flush()
  assert.equal(reads, 1); assert.equal(page.findAll('detail-set').length, 2)
  assert.match(page.text(), /组间歇17秒/); assert.match(page.text(), /组间歇未记录/); assert.match(page.text(), /22.5kg/)
})

test('custom creation permits unknown metadata with inline notice only and keeps errors in the form', async () => {
  const p = platform(), clock = manualClock(); let posted, added = 0, denied = true
  const page = runtime('../../src/components/CustomExerciseEntry.tsx', {
    '@tarojs/taro': p.module, '../core/request': { errorMessage: e => e.message },
    '../services/exercises': { exerciseApi: { custom: async () => [], createCustom: async body => { posted = body; if (denied) throw new Error('已知冲突，不能添加'); return { exercise_id: 'custom', exercise_name: body.name } } } }
  }, clock.globals)
  // This component is normally rendered by either plan page.
  const component = page.exports.default
  page.exports.default = () => component({ onAdd: () => { added++ } })
  page.render(); await page.click('open-custom-exercise'); await page.flush()
  assert.ok(page.find('custom-safety-notice'))
  assert.equal(page.findAll('custom-exercise-input').length, 3)
  page.input('custom-exercise-name', '完全不存在的新动作'); page.input('custom-exercise-description', '用户描述的方法'); await page.flush()
  page.input('custom-exercise-muscles', '背部、手臂'); page.input('custom-exercise-contraindications', ''); await page.flush()
  assert.equal(page.find('custom-exercise-name').props.value, '完全不存在的新动作')
  assert.equal(page.find('custom-exercise-muscles').props.value, '背部、手臂')
  await page.click('create-custom-exercise'); await page.flush(); assert.equal(added, 0); assert.match(page.text(), /已知冲突/)
  denied = false; await page.click('create-custom-exercise'); await page.flush()
  assert.equal(added, 1); assert.deepEqual(posted.contraindications, []); assert.deepEqual(posted.muscles, ['背部', '手臂'])
  assert.ok(page.find('exercise-sheet-closing'))
  clock.advance(260); await page.flush()
  assert.equal(page.find('exercise-sheet-layer').props.style.display, 'none')
  assert.equal(page.find('custom-exercise-name').props.value, '')
  assert.equal(clock.count(), 0)
})

test('rest duration survives background time, early stop, retry and set-save recovery', () => {
  const p = platform()
  const rest = runtime('../../src/core/workout-rest.ts', { '@tarojs/taro': p.module }).exports
  const value = rest.newRest('s', exercise, 1, 100000)
  rest.saveRest(value)
  assert.equal(rest.recoverRest({ ...session, status: 'in_progress' }).startedAt, 100000)
  const ended = rest.endRest(value, 'next_set', 117000)
  rest.saveRest(ended)
  assert.equal(rest.restPayload(rest.endRest(ended, 'next_set', 200000)).actual_rest_seconds, 17)
  assert.equal(rest.recoverRest({ ...session, status: 'in_progress' }).endedAt, 117000)
  assert.equal(rest.recoverRest({ ...session, status: 'completed' }), null)
  assert.equal(p.storage.size, 0)
  assert.equal(rest.recoverRest({ ...session, status: 'in_progress' }), null)
})

test('active page captures end time before network and reuses it after failed rest save', async () => {
  const p = platform(); let now = 100000, fail = true
  let current = { ...session, status: 'in_progress', total_sets: 0, exercises: [exercise] }
  const calls = []
  class Clock extends Date { static now () { return now } }
  const page = runtime('../../src/pages/workout-active/index.tsx', {
    '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message },
    '../../services/workouts': { workoutApi: { active: async () => current, plans: async () => [],
      recordSet: async (_s, _e, number, reps, weight, finished) => { current = { ...current, total_sets: 1, exercises: [{ ...exercise, sets_data: [{ set_number: number, reps, weight_kg: weight, rest_started_at: finished }] }] }; return current },
      recordRest: async (_s, _e, _n, data) => { calls.push(data); if (fail) throw new Error('断网'); return current }
    } }
  }, { Date: Clock, setInterval: () => 1, clearInterval: () => {} })
  page.render(); p.hooks.load({}); await page.flush()
  await page.click('set-save'); await page.flush()
  assert.ok(page.find('rest-bar'))
  now = 117000; await page.click('end-rest'); await page.flush(); assert.match(page.text(), /重试保存休息/)
  now = 160000; fail = false; await page.click('end-rest'); await page.flush()
  assert.deepEqual(calls[0], calls[1]); assert.equal(calls[0].actual_rest_seconds, 17)
  assert.equal(page.find('rest-bar'), undefined)
})

for (const target of ['plan-editor', 'plan-builder']) {
  test(`${target} accepts a private exercise and submits its ID without auto-confirming a proposal`, async () => {
    const p = platform(), clock = manualClock(); const sent = []
    const original = { exercise_id: 'e', exercise_name: '公共动作', category: '力量', day_of_week: 1, sets: 2, reps: '8', rest_seconds: 90, order_index: 0 }
    const preview = { name: '训练计划', goal: 'general_fitness', duration_weeks: 4, days_per_week: 1, session_duration_min: 45, exercises: [original], exercise_options: [{ ...original, difficulty: '初级', equipment: [] }], rationale: [], safety_notes: [] }
    const context = { proposals_enabled: true, active_session: false, base_plan_fingerprint: 'a'.repeat(64), base_plan: { ...preview, training_days: [1], exercises: [{ ...original, item_key: 'planned:original', recommended_weight_kg: null }] }, exercise_options: preview.exercise_options }
    p.module.default.disableAlertBeforeUnload = () => {}
    p.module.default.reLaunch = async value => { p.navigations.push(value) }
    const page = runtime(`../../src/pages/${target}/index.tsx`, {
      '../../components/ExercisePicker': { __esModule: true, default: () => null },
      '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message }, '../core/request': { errorMessage: e => e.message },
      '../../services/profile': { profileApi: { get: async () => ({ onboarding_completed: true, training_days_per_week: 1, session_duration_min: 45 }) } },
      '../../services/workouts': { workoutApi: { previewPersonalizedPlan: async () => preview, confirmPersonalizedPlan: async data => { sent.push(data); return plan } } },
      '../../services/plan-management': { planManagementApi: { editContext: async () => context, createAdjustment: async (_id, _fp, data) => { sent.push(data); return { id: 'proposal' } } } },
      '../services/exercises': { exerciseApi: { custom: async () => [], createCustom: async () => ({ exercise_id: 'private', exercise_name: '全新自定义动作', category: '力量', difficulty: '未知', equipment: [], safety_notice: '本平台仅提供记录与计划管理，请自行核对动作方法、训练负荷及身体适用性；如有疑问，请咨询专业人士。' }) } }
    }, clock.globals)
    page.render(); p.hooks.load({ id: 'p' }); await page.flush()
    await page.click('open-custom-exercise'); await page.flush()
    page.input('custom-exercise-name', '全新自定义动作'); page.input('custom-exercise-description', '方法'); await page.flush()
    await page.click('create-custom-exercise'); await page.flush()
    assert.equal(sent.length, 0)
    assert.ok(page.find('exercise-sheet-closing'))
    clock.advance(260); await page.flush()
    assert.equal(page.find('exercise-sheet-layer').props.style.display, 'none')
    assert.equal(clock.count(), 0)
    assert.ok(page.find('custom-safety-notice'))
    await page.click(target === 'plan-editor' ? 'save-proposal' : 'save-button')
    assert.equal(sent.length, 1)
    assert.equal(sent[0].exercises[1].exercise_id, 'private')
    if (target === 'plan-editor') {
      assert.equal(sent[0].exercises[1].safety_notice, undefined)
      assert.deepEqual(p.navigations, [{ url: '/pages/plan-proposal-detail/index?id=proposal' }])
    }
  })
}

test('lost set response cannot be replaced by another set and reload resumes the original rest', async () => {
  const p = platform(); let writes = 0, now = 100000
  class Clock extends Date { static now () { return now } }
  let current = { ...session, status: 'in_progress', total_sets: 0, exercises: [exercise] }
  const page = runtime('../../src/pages/workout-active/index.tsx', {
    '@tarojs/taro': p.module, '../../core/request': { errorMessage: e => e.message },
    '../../services/workouts': { workoutApi: { active: async () => current, plans: async () => [],
      recordSet: async (_s, _e, number, reps, weight, finished) => {
        writes++
        const prior = current.exercises[0].sets_data[0] || {}
        current = { ...current, total_sets: 1, exercises: [{ ...exercise, sets_data: [{ ...prior, set_number: number, reps, weight_kg: weight, ...(finished ? { rest_started_at: finished } : {}) }] }] }
        if (writes === 1) throw new Error('提交后连接中断')
        return current
      },
      recordRest: async (_s, _e, _n, data) => { current.exercises[0].sets_data[0] = { ...current.exercises[0].sets_data[0], rest_event_id: data.event_id, actual_rest_seconds: data.actual_rest_seconds }; return current }
    } }
  }, { Date: Clock, setInterval: () => 1, clearInterval: () => {} })
  page.render(); p.hooks.load({}); await page.flush()
  await page.click('set-save'); await page.flush(); assert.equal(writes, 1)
  await page.findAll('set-save')[1].props.onClick(); await page.flush(); assert.equal(writes, 1)
  assert.match(page.text(), /上一组的保存结果尚未核对/)
  now = 117000; await page.click('reload-workout'); await page.flush(); assert.ok(page.find('rest-bar'))
  await page.click('end-rest'); await page.flush(); assert.equal(page.find('rest-bar'), undefined)
  page.input('set-input', '30'); await page.flush()
  await page.click('set-save'); await page.flush()
  assert.equal(writes, 2); assert.equal(page.find('rest-bar'), undefined)
  assert.equal(current.exercises[0].sets_data[0].actual_rest_seconds, 17)
})
