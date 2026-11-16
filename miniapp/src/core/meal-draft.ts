import type { Food, MealItemInput } from '../types/api'

export const nutrientKeys = ['calories', 'protein_g', 'carbs_g', 'fat_g'] as const
export type NutrientKey = typeof nutrientKeys[number]
type Nutrition = Record<NutrientKey, number>
export interface MealDraftItem {
  key: string
  food_id?: string | null
  custom_food_id?: string | null
  custom_food_version?: number | null
  food_name: string
  amountText: string
  nutrients: Record<NutrientKey, string>
  invalidNutrition?: Partial<Record<NutrientKey, string>>
  // Immutable across amount edits. Never scale the last rounded preview.
  basis: { amount: number, nutrients: Nutrition }
}

export function parseMealNumber (text: string, max: number, allowZero = true): number | null {
  if (!/^(?:\d+|\d*\.\d+)$/.test(text.trim())) return null
  const value = Number(text)
  return Number.isFinite(value) && value <= max && (allowZero ? value >= 0 : value > 0) ? value : null
}

const texts = (n: Nutrition) => Object.fromEntries(nutrientKeys.map(key => [key, String(n[key])])) as MealDraftItem['nutrients']
export const nutrientLimit = (key: NutrientKey) => key === 'calories' ? 50000 : 5000

export function existingMealDraft (item: MealItemInput, key: string): MealDraftItem {
  const nutrients = { calories: item.calories, protein_g: item.protein_g, carbs_g: item.carbs_g, fat_g: item.fat_g }
  return { key, food_id: item.food_id, food_name: item.food_name, amountText: String(item.amount_g), nutrients: texts(nutrients), basis: { amount: item.amount_g, nutrients } }
}

export function foodMealDraft (food: Food, grams: number, key: string): MealDraftItem {
  const item = existingMealDraft({ food_id: food.source === 'custom' ? undefined : food.id, food_name: food.name_zh, amount_g: 100,
    calories: food.calories_per_100g, protein_g: food.protein_g, carbs_g: food.carbs_g, fat_g: food.fat_g }, key)
  if (food.source === 'custom') {
    item.custom_food_id = food.id
    item.custom_food_version = food.version
  }
  return changeMealAmount(item, String(grams))
}

export function changeMealAmount (item: MealDraftItem, raw: string): MealDraftItem {
  const amount = parseMealNumber(raw, 10000, false)
  if (amount === null) return { ...item, amountText: raw }
  if (Object.keys(item.invalidNutrition || {}).length) return { ...item, amountText: raw }
  const nutrients = Object.fromEntries(nutrientKeys.map(key => [key, Number((item.basis.nutrients[key] * amount / item.basis.amount).toFixed(6))])) as Nutrition
  return { ...item, amountText: raw, nutrients: { ...texts(nutrients), ...item.invalidNutrition } }
}

export function changeMealNutrition (item: MealDraftItem, key: NutrientKey, raw: string): MealDraftItem {
  if (item.food_id) return item
  const invalidNutrition = { ...item.invalidNutrition }
  if (parseMealNumber(raw, nutrientLimit(key)) === null) invalidNutrition[key] = raw
  else delete invalidNutrition[key]
  // A deliberate manual change is a meal-only snapshot, never a library update.
  const next = { ...item, custom_food_id: undefined, custom_food_version: undefined, invalidNutrition, nutrients: { ...item.nutrients, [key]: raw } }
  const candidate = mealDraftCandidate(next)
  // Partial/invalid input stays visible but can never reset the valid basis.
  if (!candidate) return next
  return { ...next, basis: { amount: candidate.amount_g, nutrients: {
    calories: candidate.calories, protein_g: candidate.protein_g, carbs_g: candidate.carbs_g, fat_g: candidate.fat_g
  } } }
}

export function mealDraftCandidate (item: MealDraftItem): MealItemInput | null {
  const amount = parseMealNumber(item.amountText, 10000, false)
  if (amount === null || !item.food_name.trim() || item.food_name.trim().length > 100) return null
  const values = nutrientKeys.map(key => parseMealNumber(item.nutrients[key], nutrientLimit(key)))
  if (values.some(value => value === null)) return null
  return { ...(item.food_id ? { food_id: item.food_id } : {}), food_name: item.food_name.trim(), amount_g: amount,
    ...(item.custom_food_id ? { custom_food_id: item.custom_food_id, custom_food_version: item.custom_food_version } : {}),
    calories: values[0]!, protein_g: values[1]!, carbs_g: values[2]!, fat_g: values[3]! }
}
