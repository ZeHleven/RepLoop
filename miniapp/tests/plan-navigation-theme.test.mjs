import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred } from './helpers/page-runtime.mjs'
import { interactionRuntime } from './helpers/interaction-runtime.mjs'

function fixture (target) {
  const response = deferred(), writes = []
  const exercise = { item_key: 'a', exercise_id: 'row', exercise_name: '划船', category: '力量', day_of_week: 2, sets: 3, reps: '8', rest_seconds: 90, recommended_weight_kg: null, order_index: 0 }
  const plan = { name: '计划', duration_weeks: 4, training_days: [2], days_per_week: 1, session_duration_min: 45, exercises: [exercise], exercise_options: [], rationale: [], safety_notes: [] }
  const page = interactionRuntime(`../../src/pages/${target}/index.tsx`, {
    '../../components/CustomExerciseEntry': { __esModule: true, default: props => ({ type: 'CustomEntry', props: { ...props, className: 'custom-entry-test' } }) },
    '../../core/request': { errorMessage: e => e.message },
    '../../services/plan-management': { planManagementApi: {
      editContext: () => response.promise, createAdjustment: (...args) => { writes.push(args); throw new Error('Unexpected write') }
    } },
    '../../services/profile': { profileApi: { get: async () => ({ onboarding_completed: true, training_days_per_week: 1, session_duration_min: 45 }) } },
    '../../services/workouts': { workoutApi: { previewPersonalizedPlan: () => response.promise, confirmPersonalizedPlan: (...args) => { writes.push(args); throw new Error('Unexpected write') } } }
  }, { defineAppConfig: config => config, definePageConfig: config => config })
  page.render()
  const app = page.loadSource('../../src/app.config.ts').default
  const config = page.loadSource(`../../src/pages/${target}/index.config.ts`).default
  const assertTheme = () => {
    const meta = page.find('plan-sheet-meta'), nav = page.find('plan-navigation-bar')
    assert.ok(meta, 'theme must also render while loading or after failure')
    assert.ok(nav, 'PageMeta must explicitly set NavigationBar instead of falling back to white/black')
    assert.equal(meta.type, 'PageMeta'); assert.equal(nav.type, 'NavigationBar')
    const children = [meta.props.children].flat()
    assert.equal(children[0], nav, 'navigation node is the first child of PageMeta')
    assert.equal(nav.props.backgroundColor, app.window.navigationBarBackgroundColor)
    assert.equal(nav.props.backgroundColor, '#163d2b')
    assert.equal(nav.props.frontColor, '#ffffff'); assert.equal(app.window.navigationBarTextStyle, 'white')
    assert.equal(nav.props.title, config.navigationBarTitleText)
    assert.equal(nav.props.colorAnimationDuration, '0', 'no unrelated navigation color animation')
  }
  return { ...page, response, writes, assertTheme, data: target === 'plan-editor' ? { base_plan: plan, base_plan_fingerprint: 'base', exercise_options: [], proposals_enabled: true } : plan }
}

for (const target of ['plan-editor', 'plan-builder']) {
  test(`${target}: native navigation keeps the global theme while loading, opening/closing sheet and returning`, async () => {
    const page = fixture(target)
    page.assertTheme()
    page.hooks.useLoad({ id: 'plan' }); await page.flush(); page.assertTheme()
    page.response.resolve(page.data); await page.flush(); page.assertTheme()
    for (const open of [true, false, true, false]) {
      page.find('custom-entry-test').props.onOpenChange(open); await page.flush()
      page.assertTheme(); assert.equal(page.find('plan-sheet-meta').props.pageStyle, open ? 'overflow: hidden;' : '')
    }
    page.hooks.useDidHide?.(); await page.flush(); page.hooks.useDidShow?.(); await page.flush()
    page.assertTheme(); assert.equal(page.writes.length, 0); page.unmount()
  })

  test(`${target}: a failed load does not revert navigation to white/black`, async () => {
    const page = fixture(target)
    page.hooks.useLoad({ id: 'plan' }); await page.flush()
    page.response.reject(new Error('断网')); await page.flush()
    assert.match(page.text(), /断网/); page.assertTheme()
    assert.equal(page.writes.length, 0); page.unmount()
  })
}
