export function nextTrainingDay (
  plannedDays: number[],
  date: Date = new Date()
): number {
  const uniqueDays = [...new Set(plannedDays)].filter(day => day >= 1 && day <= 7)
  if (!uniqueDays.length) throw new Error('训练计划没有可开始的训练日')
  const today = date.getDay() || 7
  return uniqueDays.reduce((best, candidate) => {
    const bestDistance = (best - today + 7) % 7
    const candidateDistance = (candidate - today + 7) % 7
    return candidateDistance < bestDistance ? candidate : best
  })
}
