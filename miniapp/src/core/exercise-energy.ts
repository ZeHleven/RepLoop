import type { EnergyCategory, SessionEnergyUpdate, WorkoutSession } from '../types/api'

export const energyCategories: { label: string, value: EnergyCategory | null }[] = [
  { label: '未填写（不启用估算）', value: null },
  { label: '普通抗阻训练', value: 'resistance_training' },
  { label: '普通自重抗阻', value: 'bodyweight_resistance' }
]
export const energyCategoryLabel = (category?: EnergyCategory | null) => energyCategories.find(row => row.value === category)?.label || energyCategories[0].label
export const energyCategoryHelp = '普通抗阻：哑铃、杠铃、器械、弹力带等常规分组训练；普通自重抗阻：分组俯卧撑、深蹲等。不适用于跑步、骑行或 HIIT。分类仅用于整场消耗粗估，不代表健康适用性认证。'

export function classificationsApplied (session: WorkoutSession, request: SessionEnergyUpdate): boolean {
  if (session.energy_classification_version !== request.expected_version + 1) return false
  return request.exercises.every(change => {
    const row = session.exercises.find(item => item.id === change.session_exercise_id)
    return row?.energy_category === change.energy_category && (!request.update_future || (
      row.library_energy_category === change.energy_category &&
      row.library_energy_category_version === (change.expected_exercise_version ?? -1) + 1
    ))
  })
}
export function trainingTimeLabel (startedAt?: string | null, date?: string | null): string {
  if (!startedAt) return date || '时间未记录'
  const value = new Date(startedAt)
  if (!Number.isFinite(value.getTime())) return date || '时间未记录'
  return new Date(value.getTime() + 8 * 3600000).toISOString().slice(0, 16).replace('T', ' ') + '（北京时间）'
}
