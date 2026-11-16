import type { PersonalizedExerciseOption } from '../types/api'

export const exerciseBodyParts = ['全部', '胸部', '背部', '肩部', '手臂', '臀部', '腿部', '核心']
export const normalizeExerciseQuery = (value: string) => value.normalize('NFKC').toLowerCase().replace(/[\s\-·()（）]/g, '')
export function filterExerciseOptions (options: PersonalizedExerciseOption[], part: string, query: string) {
  const tokens = query.trim().split(/\s+/).map(normalizeExerciseQuery).filter(Boolean)
  return options.filter(option => {
    if (part !== '全部' && !(option.body_parts || []).includes(part)) return false
    const text = normalizeExerciseQuery([option.exercise_name, ...(option.search_aliases || []), ...(option.body_parts || [])].join(' '))
    return tokens.every(token => text.includes(token))
  })
}
const equipmentNames: Record<string, string> = {
  barbell: '杠铃', dumbbell: '哑铃', bodyweight: '自重', cable: '绳索', machine: '器械', bench: '训练凳',
  pull_up_bar: '单杠', resistance_band: '弹力带', ez_bar: '曲杠', smith_machine: '史密斯机',
  leg_press_machine: '腿举机', leg_curl_machine: '腿弯举机', t_bar_row_machine: 'T 杠划船机',
  chest_supported_row_machine: '胸托划船机', high_row_machine: '高位划船机', plate_loaded_pulldown_machine: '挂片下拉机'
}
export function exerciseOptionDescription (option: PersonalizedExerciseOption) {
  return [...(option.body_parts || []).slice(0, 1), ...(option.equipment || []).map(value => equipmentNames[value] || value)].join(' · ')
}
