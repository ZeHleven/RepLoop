import type { Food, CustomFoodValues } from '../types/api'
import { parseMealNumber, nutrientLimit, nutrientKeys } from './meal-draft'

export type EnergyUnit = 'kJ' | 'kcal'
export const emptyPackagedFood = () => ({ name: '', amount: '100', calories: '', protein: '', carbs: '', fat: '', unit: 'kJ' as EnergyUnit })
export type PackagedFoodForm = ReturnType<typeof emptyPackagedFood>
export function packagedFoodValues (form: PackagedFoodForm) {
  const amount = parseMealNumber(form.amount, 10000, false)
  const energy = parseMealNumber(form.calories, form.unit === 'kJ' ? 50000 * 4.184 : 50000)
  const values = [energy === null ? null : energy / (form.unit === 'kJ' ? 4.184 : 1),
    parseMealNumber(form.protein, 5000), parseMealNumber(form.carbs, 5000), parseMealNumber(form.fat, 5000)]
  if (amount === null || values.some(v => v === null)) return null
  const totals = values.map(v => v! * amount / 100)
  if (totals.some((v, i) => !Number.isFinite(v) || v > nutrientLimit(nutrientKeys[i]))) return null
  const data: CustomFoodValues = { name: form.name.trim(), amount_g: 100, calories: values[0]!, protein_g: values[1]!, carbs_g: values[2]!, fat_g: values[3]! }
  const warnings: string[] = []
  if (data.carbs_g + data.protein_g + data.fat_g > 100.5) warnings.push('营养克数明显偏高，请核对是否填成整包总量。')
  if (data.calories > 0 && Math.abs((data.carbs_g * 4 + data.protein_g * 4 + data.fat_g * 9) / data.calories - 1) > .2) warnings.push('热量与三大营养素换算差异较大，请核对标签；不会自动改写。')
  return { amount, data, warnings, totals: { calories: totals[0], protein: totals[1], carbs: totals[2], fat: totals[3] } }
}
export function convertPackagedEnergy (form: PackagedFoodForm, unit: EnergyUnit): PackagedFoodForm {
  const n = parseMealNumber(form.calories, 50000 * 4.184)
  if (form.unit === unit) return form
  return { ...form, unit, calories: n === null ? form.calories : String(Number((n * (unit === 'kJ' ? 4.184 : 1 / 4.184)).toPrecision(12))) }
}
export function editPackagedFood (food: Food): PackagedFoodForm {
  // The API's per100 fields are already normalized from the original basis.
  // Reading/editing must never write back or scale the existing meal draft.
  return { name: food.basis?.name || food.name_zh, amount: String(food.basis?.amount_g || 100), unit: 'kcal',
    calories: String(food.calories_per_100g), protein: String(food.protein_g), carbs: String(food.carbs_g), fat: String(food.fat_g) }
}
export const foodBrowseCategories = ['主食薯类', '肉蛋类', '鱼虾水产', '豆类豆制品', '奶类', '蔬菜菌菇', '水果', '坚果油脂'] as const
export const foodPageSize = 12
