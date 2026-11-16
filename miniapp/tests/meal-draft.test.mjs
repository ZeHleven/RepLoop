import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'
const draft = runtime('../../src/core/meal-draft.ts').exports
const source = { food_name: '自制餐', amount_g: 100, calories: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }
test('opening an existing custom meal preserves its exact stored precision', () => {
  const original = { ...source, calories: 130.123, fat_g: .045 }
  assert.deepEqual(draft.mealDraftCandidate(draft.existingMealDraft(original, 'row')), original)
})

test('fixed basis survives incomplete input, decimal grams and repeated rounding', () => {
  let item = draft.existingMealDraft(source, 'stable')
  for (const value of ['', '1.', '0', '-1', '10001', 'NaN', 'Infinity']) {
    const invalid = draft.changeMealAmount(item, value)
    assert.equal(draft.mealDraftCandidate(invalid), null)
    item = draft.changeMealAmount(invalid, '150')
    assert.equal(draft.mealDraftCandidate(item).calories, 195)
  }
  for (let i = 0; i < 100; i++) {
    item = draft.changeMealAmount(item, '33.3')
    item = draft.changeMealAmount(item, '100')
  }
  assert.equal(draft.mealDraftCandidate(item).calories, 130)
  assert.equal(draft.mealDraftCandidate(draft.changeMealAmount(item, '.5')).amount_g, .5)
  assert.equal(item.key, 'stable')
})

test('custom nutrition edits replace the basis only with a valid complete portion', () => {
  let item = draft.existingMealDraft(source, 'stable')
  item = draft.changeMealNutrition(item, 'calories', '')
  assert.equal(draft.mealDraftCandidate(item), null)
  item = draft.changeMealAmount(item, '150')
  assert.equal(draft.mealDraftCandidate(item), null)
  item = draft.changeMealNutrition(item, 'calories', '300')
  item = draft.changeMealAmount(item, '100')
  assert.equal(draft.mealDraftCandidate(item).calories, 200)
  item = draft.changeMealNutrition(item, 'fat_g', '0')
  assert.equal(draft.mealDraftCandidate(item).fat_g, 0)
  item = draft.changeMealNutrition(item, 'fat_g', '5001')
  assert.equal(draft.mealDraftCandidate(item), null)
})

test('standard source cannot accept edited nutrition; payload strips UI metadata', () => {
  const item = draft.foodMealDraft({ id: 'rice', name_zh: '杂粮饭', calories_per_100g: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }, 150, 'row')
  assert.equal(draft.changeMealNutrition(item, 'calories', '0'), item)
  assert.deepEqual(draft.mealDraftCandidate(item), { food_id: 'rice', food_name: '杂粮饭', amount_g: 150, calories: 195, protein_g: 4.5, carbs_g: 42, fat_g: 1.5 })
})
