import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'

const food = { id: 'rice', name_zh: '杂粮饭', calories_per_100g: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }
const summary = { date: '2026-09-07', meals: [], total_calories: 0, total_protein_g: 0, total_carbs_g: 0, total_fat_g: 0 }

test('clearing then retyping grams preserves the original nutrition basis', async () => {
  const saved = []
  const page = runtime('../../src/pages/nutrition/index.tsx', {
    '../../services/nutrition': { nutritionApi: {
      today: async () => summary, history: async () => [], foods: async () => [food],
      logMeal: async value => { saved.push(value); return value }
    } },
    '../../core/request': { errorMessage: e => e.message },
    '../../services/profile': { profileApi: { update: async () => {} } }
  })
  page.render(); page.hooks.useDidShow(); await page.flush()
  if (page.find('start-meal')) { page.click('start-meal'); await page.flush() }
  page.click('food-add'); await page.flush()
  page.input('selected-amount-input', ''); await page.flush()
  page.input('selected-amount-input', '150'); await page.flush()
  await page.click('save-meal')
  assert.equal(saved.length, 1)
  assert.equal(saved[0].items[0].calories, 195)
  assert.equal(saved[0].items[0].carbs_g, 42)
})

test('workout history exposes the persisted adaptive proposal without writing', async () => {
  const page = runtime('../../src/pages/history/index.tsx', {
    '../../services/workouts': { workoutApi: {
      history: async () => [{ id: 'session', status: 'completed', trained_at: '2026-09-07', exercises: [],
        total_sets: 4, total_reps: 32, total_volume_kg: 1600,
        adjustments: [{ exercise_id: 'bench', exercise_name: '卧推', reason: '调整建议' }],
        adaptive_adjustment_status: 'pending_confirmation',
        adaptive_adjustment_proposal: { id: 'proposal', status: 'pending_confirmation' } }],
      progress: async () => null
    } },
    '../../core/request': { errorMessage: e => e.message }
  })
  page.render(); page.hooks.useDidShow(); await page.flush()
  assert.ok(page.find('adaptive-proposal-link'))
})

test('refresh 503 preserves credentials and pending Agent state', async () => {
  let cleared = 0, redirects = 0
  const page = runtime('../../src/core/request.ts', {
    './storage': { getAccessToken: () => 'access', getRefreshToken: () => 'refresh', saveTokens: () => {}, clearTokens: () => { cleared++ } },
    '@tarojs/taro': { __esModule: true, default: {
      request: async options => ({ statusCode: options.url.endsWith('/auth/refresh') ? 503 : 401, data: { detail: '暂时不可用' } }),
      reLaunch: async () => { redirects++ }
    } }
  }, { __FITNESS_API_BASE_URL__: 'https://test.invalid/api/v1', __FITNESS_CLOUD_ENV__: '', __FITNESS_CLOUD_SERVICE__: '' })
  await assert.rejects(page.exports.apiRequest('/profile'))
  assert.equal(cleared, 0)
  assert.equal(redirects, 0)
})
