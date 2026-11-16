import { useEffect, useRef, useState } from 'react'
import { Button, Input, RootPortal, ScrollView, Text, View } from '@tarojs/components'
import type { PersonalizedExerciseOption } from '../types/api'
import { useExerciseSheet } from '../core/use-exercise-sheet'
import { exerciseBodyParts, exerciseOptionDescription, filterExerciseOptions } from '../core/exercise-search'
import './custom-exercise.scss'
import './exercise-picker.scss'

type Props = {
  options: PersonalizedExerciseOption[]
  selectedIds: string[]
  currentId?: string
  dayLabel: string
  label: string
  disabled?: boolean
  onSelect: (option: PersonalizedExerciseOption) => void
  onOpenChange?: (open: boolean) => void
}

export default function ExercisePicker ({ options, selectedIds, currentId, dayLabel, label, disabled, onSelect, onOpenChange }: Props) {
  const sheet = useExerciseSheet(onOpenChange)
  const [query, setQuery] = useState('')
  const [part, setPart] = useState('全部')
  const [appliedQuery, setAppliedQuery] = useState('')
  const selecting = useRef(false)
  const currentOptions = useRef(options); currentOptions.current = options
  useEffect(() => {
    const timer = setTimeout(() => setAppliedQuery(query), 120)
    return () => clearTimeout(timer)
  }, [query])
  useEffect(() => { if (disabled && sheet.isOpen()) sheet.dismiss(true) }, [disabled])
  const filtered = filterExerciseOptions(options, part, appliedQuery)
  const show = () => { if (!disabled && sheet.open()) selecting.current = false }
  const select = (option: PersonalizedExerciseOption) => {
    if (disabled || !sheet.isOpen() || selecting.current || selectedIds.includes(option.exercise_id)) return
    const fresh = currentOptions.current.find(row => row.exercise_id === option.exercise_id)
    if (!fresh) return
    selecting.current = true
    onSelect(fresh)
    sheet.dismiss()
  }
  return <View className='exercise-picker-entry'>
    <Button className='secondary-button open-exercise-picker' disabled={disabled || sheet.phase !== 'closed'} onClick={show}>{label}</Button>
    {sheet.created && <RootPortal className='exercise-picker-portal'>
      <View className={`exercise-sheet-layer exercise-sheet-${sheet.phase}`} style={{ display: sheet.phase === 'closed' ? 'none' : 'block' }}>
        <View className='exercise-sheet-backdrop' catchMove onClick={() => sheet.dismiss()} />
        <View className='exercise-sheet-panel library-picker-panel' style={sheet.panelStyle}>
          <View className='library-picker-grip' />
          <View className='exercise-sheet-header library-picker-header' catchMove>
            <View className='exercise-sheet-heading'><Text className='custom-title'>{currentId ? '替换动作' : '添加动作'}</Text><Text className='custom-help'>{dayLabel}</Text></View>
            <Button className='close-exercise-picker' onClick={() => sheet.dismiss()}>取消</Button>
          </View>
          <View className='library-picker-filters' catchMove>
            <View className='library-search-field'>
              <Input className='library-search-input' value={query} maxlength={100} adjustPosition={false} confirmType='search'
                placeholder='搜索动作名称或别名' onInput={event => setQuery(event.detail.value)} onConfirm={() => setAppliedQuery(query)} />
              {query && <Button className='clear-exercise-search' aria-label='清空搜索' onClick={() => { setQuery(''); setAppliedQuery('') }}>×</Button>}
            </View>
            <View className='library-body-parts'>
              {exerciseBodyParts.map(value => <Button key={value} className={`library-body-part ${part === value ? 'selected' : ''}`} aria-pressed={part === value} onClick={() => setPart(value)}>{value}</Button>)}
            </View>
          </View>
          <View className='library-results-meta' catchMove><Text>{part === '全部' ? '全部动作' : part} · {filtered.length} 项</Text><Text>点击选择</Text></View>
          <ScrollView className='exercise-sheet-scroll library-results-scroll' scrollY enhanced showScrollbar={false} bounces={false} key={`${part}-${appliedQuery}`}>
            <View className='library-result-list'>
              {filtered.map(option => <Button key={option.exercise_id} className='library-result' disabled={disabled || selecting.current || selectedIds.includes(option.exercise_id)} onClick={() => select(option)}>
                <View className='library-result-copy'><Text className='library-result-name'>{option.exercise_name}</Text><Text className='library-result-description'>{exerciseOptionDescription(option)}</Text></View>
                <Text className={`library-result-action ${selectedIds.includes(option.exercise_id) ? 'included' : ''}`}>{option.exercise_id === currentId ? '当前' : selectedIds.includes(option.exercise_id) ? '已添加' : '+'}</Text>
              </Button>)}
            </View>
            {!filtered.length && <View className='library-empty'><Text>没有找到匹配动作</Text><Text className='library-empty-help'>换个名称，或试试其他部位。</Text><Button className='reset-exercise-search' onClick={() => { setPart('全部'); setQuery(''); setAppliedQuery('') }}>清除筛选</Button></View>}
          </ScrollView>
        </View>
      </View>
    </RootPortal>}
  </View>
}
