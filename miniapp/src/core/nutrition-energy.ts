export function energyText (value?: number | null): string {
  return value == null || !Number.isFinite(value) ? '—' : `约 ${Math.round(Math.abs(value) / 10) * 10} kcal`
}
export function balanceLabel (value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '预计缺口／盈余'
  if (Math.round(Math.abs(value) / 10) === 0) return '预计持平'
  return value < 0 ? '预计缺口' : '预计盈余'
}
export function macroPercent (grams: number, factor: number, calories: number): string {
  return calories > 0 && Number.isFinite(grams) && Number.isFinite(calories)
    ? `${(grams * factor / calories * 100).toFixed(1)}%` : '—'
}
export function macroEnergyMismatch (protein: number, carbs: number, fat: number, calories: number): boolean {
  const total = protein * 4 + carbs * 4 + fat * 9
  if (![total, calories].every(Number.isFinite)) return true
  if (calories <= 0) return total > 0
  // A display-only data-quality hint, not a nutrition target or a save restriction.
  return Math.abs(total - calories) > calories * 0.2
}
export function nutritionDate (daysAgo = 0): string {
  return new Date(Date.now() + 8 * 3600000 - daysAgo * 86400000).toISOString().slice(0, 10)
}
