import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'

test('energy presentation rounds absolute values, treats missing as unknown and does not normalize macros', () => {
  const model = runtime('../../src/core/nutrition-energy.ts').exports
  assert.equal(model.energyText(null), '—')
  assert.equal(model.energyText(1784), '约 1780 kcal')
  assert.equal(model.balanceLabel(-4), '预计持平')
  assert.equal(model.balanceLabel(-6), '预计缺口')
  assert.equal(model.balanceLabel(6), '预计盈余')
  assert.equal(model.macroPercent(50, 4, 1000), '20.0%')
  assert.equal(model.macroPercent(10, 9, 0), '—')
  assert.equal(model.macroPercent(150, 9, 1000), '135.0%')
  assert.equal(model.macroEnergyMismatch(10, 10, 10, 100), true)
  assert.equal(model.macroEnergyMismatch(10, 10, 2, 100), false)
  assert.equal(model.macroEnergyMismatch(0, 0, 0, 0), false)
  assert.equal(model.macroEnergyMismatch(10, 0, 0, 0), true)
})

test('private library selection carries version; historical edit and explicit snapshot edits do not relink it', () => {
  const draft = runtime('../../src/core/meal-draft.ts').exports
  const food = { id: 'private', source: 'custom', version: 3, name_zh: '自制餐', calories_per_100g: 120, protein_g: 10, carbs_g: 11, fat_g: 4 }
  let row = draft.foodMealDraft(food, 150, 'row')
  assert.equal(row.custom_food_id, 'private')
  assert.equal(draft.mealDraftCandidate(row).custom_food_version, 3)
  row = draft.changeMealAmount(draft.changeMealAmount(row, ''), '200')
  assert.equal(draft.mealDraftCandidate(row).calories, 240)
  const snapshot = draft.existingMealDraft(draft.mealDraftCandidate(row), 'history')
  assert.equal(draft.mealDraftCandidate(snapshot).custom_food_id, undefined)
  const override = draft.changeMealNutrition(row, 'calories', '280')
  assert.equal(draft.mealDraftCandidate(override).custom_food_id, undefined)
})

test('nutrition transport keeps scoped library, explicit writes and version fields', async () => {
  const calls = []
  const api = runtime('../../src/services/nutrition.ts', { '../core/request': { apiRequest: async (...args) => { calls.push(args) } } }).exports.nutritionApi
  await api.foods('酸奶', 30, 'mine')
  await api.createFood({ name: '酸奶', client_request_id: 'request-123' })
  await api.deleteFood('food-123', 2)
  assert.deepEqual(calls[0], ['/foods/library', { query: { q: '酸奶', limit: 30, scope: 'mine', offset: 0, browse_category: undefined } }])
  assert.equal(calls[1][1].method, 'POST')
  assert.deepEqual(calls[2], ['/foods/custom/food-123', { method: 'DELETE', query: { version: 2 } }])
})

test('nutrition dates use Beijing midnight independent of device timezone', () => {
  class Clock extends Date { static now () { return Date.parse('2026-09-08T16:01:00Z') } }
  const { nutritionDate } = runtime('../../src/core/nutrition-energy.ts', {}, { Date: Clock }).exports
  assert.equal(nutritionDate(), '2026-09-09')
  assert.equal(nutritionDate(29), '2026-08-11')
})
