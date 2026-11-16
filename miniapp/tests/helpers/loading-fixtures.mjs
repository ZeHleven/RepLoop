import { interactionRuntime } from './interaction-runtime.mjs'

export function loadingFixtures () {
  const exercise = { id: 'sx', exercise_id: 'ex', exercise_name: '坐姿划船', day_of_week: 3, target_sets: 3, target_reps: '10', rest_seconds: 90, sets_data: [{ set_number: 1, reps: 10, weight_kg: 25, actual_rest_seconds: 42 }], previous_sets_data: [], energy_classification_editable: false }
  const session = { id: 's', plan_name: '力量提升 · 3日训练计划', trained_at: '2026-09-11', started_at: '2026-09-11T02:00:00Z', status: 'completed', total_sets: 1, total_reps: 10, total_volume_kg: 250, duration_min: 12, exercises: [exercise], adjustments: [], adaptive_adjustment_status: 'not_needed' }
  const progress = { weeks: 8, total_sessions: 1, total_sets: 1, total_reps: 10, total_volume_kg: 250, weekly: [{ week_start: '2026-09-07', volume_kg: 250 }] }
  const plan = { id: 'p', name: '力量提升 · 3日训练计划', display_name: '力量提升 · 3日训练计划', duration_weeks: 4, days_per_week: 3, is_active: true, exercises: [{ ...exercise, day_of_week: 1 }, { ...exercise, id:'ex2', day_of_week: 3 }, { ...exercise,id:'ex3', day_of_week:5 }], safety_status: 'compatible', safety_reasons: [], manual_proposals_enabled: true, weekly_completed_days: 1, weekly_sessions: [{ day_of_week: 1, session_id: 's', status: 'completed' }] }
  const food = { id:'f', name_zh:'燕麦', calories_per_100g:389, protein_g:17, carbs_g:66, fat_g:7 }
  const meal = { id:'m', logged_at:'2026-09-11', meal_type:'早餐', items:[{ food_id:'f', food_name:'燕麦', amount_g:100, calories:389, protein_g:17, carbs_g:66, fat_g:7 }] }
  const today = { date:'2026-09-11', total_calories:389, total_protein_g:17, total_carbs_g:66, total_fat_g:7, meals:[meal], energy_estimate:{status:'estimated', bmr_kcal:1780, total_kcal:2140, balance_kcal:-1751, reasons:[], workouts:[]} }
  return { exercise, session, progress, plan, food, today,
    profile: { onboarding_completed:true, primary_goal:'strength', experience_level:'intermediate', training_location:'gym', weight_kg:80, bmi:24.5, injuries:[], chronic_conditions:[] } }
}

export function fixtureResponse (data, path, method = 'GET', body) {
  if (method !== 'GET') {
    if (path === '/profile') { Object.assign(data.profile, body); return data.profile }
    return { id:'fixture-write', status:'applied' }
  }
  if (path === '/profile') return data.profile
  if (path === '/profile/weight') return [{ id:'w', weight_kg:80, logged_at:'2026-09-11' }]
  if (path === '/workouts/plans') return [data.plan]
  if (path === '/workouts/sessions/active') return null
  if (path === '/workouts/sessions/progress') return data.progress
  if (path === '/workouts/sessions') return [data.session]
  if (path === '/workouts/sessions/s') return data.session
  if (path === '/meals/today') return data.today
  if (path === '/meals/history') return [data.today]
  if (path === '/foods/library') return [data.food]
  if (path === '/agent/runs/run') return { id:'run', status:'completed', cards:[] }
  throw Error(`Unexpected fixture endpoint ${method} ${path}`)
}

export function loadingPage (name, { globals = {}, transport, sourceFile, deferRender = false } = {}) {
  const data = loadingFixtures(), calls = [], hooks = {}, storage = new Map([['fitness_access_token','fixture-only'],['fitness_refresh_token','fixture-only-refresh']])
  const platform = { __esModule:true,
    ...Object.fromEntries(['useLoad','useDidShow','useDidHide'].map(key => [key, fn => { hooks[key] = fn }])),
    default: {
      getStorageSync:key => storage.get(key), setStorageSync:(key,value) => storage.set(key,value), removeStorageSync:key => storage.delete(key),
      nextTick:fn => queueMicrotask(fn), pageScrollTo:async () => {}, showToast:async () => {}, reLaunch:async () => {}, navigateTo:async () => {},
      request:async options => {
        const path = new URL(options.url).pathname.replace('/api/v1','')
        calls.push({ path, method:options.method, data:options.data })
        if (transport) await transport(path, options)
        return { statusCode:200, data:JSON.parse(JSON.stringify(fixtureResponse(data,path,options.method,options.data))) }
      }
    }
  }
  const page = interactionRuntime(sourceFile || `../../src/pages/${name}/index.tsx`, { '@tarojs/taro':platform }, {
    __FITNESS_API_BASE_URL__:'http://fixture.invalid/api/v1', __FITNESS_CLOUD_ENV__:'', __FITNESS_CLOUD_SERVICE__:'', ...globals
  })
  if (!deferRender) page.render()
  return { ...page, hooks, calls, data, storage,
    cache:page.loadSource('../../src/core/read-cache.ts'),
    request:page.loadSource('../../src/core/request.ts').apiRequest,
    show () { if (name === 'workout-detail') hooks.useLoad({id:'s'}); hooks.useDidShow(); return page.flush() },
    hide () { hooks.useDidHide?.(); return page.flush() }
  }
}
