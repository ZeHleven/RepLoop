import { useEffect, useRef, useState } from 'react'
import { Button, Input, Picker, RootPortal, ScrollView, Text, Textarea, View } from '@tarojs/components'
import { exerciseApi } from '../services/exercises'
import { errorMessage } from '../core/request'
import type { PersonalizedExerciseOption } from '../types/api'
import { energyCategories, energyCategoryHelp } from '../core/exercise-energy'
import EnergyLibraryEditor from './EnergyLibraryEditor'
import { useExerciseSheet } from '../core/use-exercise-sheet'
import './custom-exercise.scss'

export const CUSTOM_EXERCISE_NOTICE = '本平台仅提供记录与计划管理，请自行核对动作方法、训练负荷及身体适用性；如有疑问，请咨询专业人士。'
const equipmentOptions = [
  ['未知 / 未填写', ''], ['自重', 'bodyweight'], ['哑铃', 'dumbbell'], ['杠铃', 'barbell'],
  ['弹力带', 'resistance_band'], ['器械', 'machine'], ['单杠', 'pull_up_bar']
]

let entrySequence = 0
interface EntryProps {
  onAdd: (option: PersonalizedExerciseOption) => void
  disabled?: boolean
  dayLabel?: string
  onOpenChange?: (open: boolean) => void
  onDraftChange?: (dirty: boolean) => void
}

export default function CustomExerciseEntry ({ onAdd, disabled = false, dayLabel, onOpenChange, onDraftChange }: EntryProps) {
  const sheet = useExerciseSheet(onOpenChange)
  const entryId = useRef('')
  if (!entryId.current) entryId.current = `custom-entry-${++entrySequence}`
  const [options, setOptions] = useState<PersonalizedExerciseOption[]>([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [muscles, setMuscles] = useState('')
  const [equipment, setEquipment] = useState(0)
  const [energyIndex, setEnergyIndex] = useState(0)
  const [contraindications, setContraindications] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [libraryBusy, setLibraryBusy] = useState(false)
  const [libraryDirty, setLibraryDirty] = useState(false)
  const busy = useRef(false)
  const libraryFlight = useRef(false)
  const loadRevision = useRef(0)
  const draftListener = useRef(onDraftChange)
  draftListener.current = onDraftChange
  const dirty = Boolean(name || description || muscles || contraindications || equipment || energyIndex || libraryDirty)
  useEffect(() => { draftListener.current?.(dirty) }, [dirty])
  useEffect(() => () => { loadRevision.current++; draftListener.current?.(false) }, [])
  const loadOptions = async () => {
    const revision = ++loadRevision.current
    setLoading(true); setError('')
    try { const rows = await exerciseApi.custom(); if (sheet.isMounted() && revision === loadRevision.current) setOptions(rows) }
    catch (e) { if (sheet.isMounted() && revision === loadRevision.current) setError(errorMessage(e, '自定义动作加载失败，可重试或创建新动作')) }
    finally { if (sheet.isMounted() && revision === loadRevision.current) setLoading(false) }
  }
  const show = async () => {
    if (disabled || !sheet.open()) return
    await loadOptions()
  }
  const close = () => {
    if (busy.current || libraryFlight.current) return
    sheet.dismiss()
  }
  const add = (option: PersonalizedExerciseOption) => {
    if (!option || !sheet.isOpen() || disabled || busy.current || libraryFlight.current) return
    onAdd(option)
    sheet.dismiss()
  }
  const create = async () => {
    if (!sheet.isOpen() || disabled || busy.current || libraryFlight.current) return
    if (!name.trim() || !description.trim()) { setError('请填写动作名称和方法；其余安全信息可留空'); return }
    busy.current = true; setSaving(true); setError('')
    const labels = (raw: string) => raw.split(/[,，、\n]/).map(x => x.trim()).filter(Boolean)
    try {
      const option = await exerciseApi.createCustom({ name: name.trim(), description: description.trim(), muscles: labels(muscles), equipment: equipmentOptions[equipment][1] ? [equipmentOptions[equipment][1]] : [], contraindications: labels(contraindications), energy_category: energyCategories[energyIndex].value })
      if (!sheet.isMounted()) return
      onAdd(option)
      sheet.dismiss(); setName(''); setDescription(''); setMuscles(''); setContraindications('')
      setEnergyIndex(0); setEquipment(0)
    } catch (e) { if (sheet.isMounted()) setError(errorMessage(e, '动作暂时无法添加，已保留填写内容')) }
    finally { busy.current = false; if (sheet.isMounted()) setSaving(false) }
  }
  const fieldId = (field: string) => `${entryId.current}-${field}`
  const frozen = saving || libraryBusy || !sheet.isOpen()
  return <View className='custom-exercise-entry'>
    <Button className='secondary-button open-custom-exercise' disabled={disabled || sheet.phase !== 'closed'} onClick={show}>＋ 自定义动作</Button>
    {sheet.created && <RootPortal className='exercise-sheet-portal'>
      <View className={`exercise-sheet-layer exercise-sheet-${sheet.phase}`} style={{ display: sheet.phase === 'closed' ? 'none' : 'block' }}>
        <View className='exercise-sheet-backdrop' catchMove onClick={() => { /* Explicit close protects long drafts from accidental dismissal. */ }} />
        <View className={`exercise-sheet-panel ${sheet.keyboardHeight ? 'exercise-sheet-keyboard' : ''}`} style={sheet.panelStyle}>
          <View className='exercise-sheet-header' catchMove>
            <View className='exercise-sheet-heading'><Text className='custom-title'>我的自定义动作</Text><Text className='custom-help'>{dayLabel ? `${dayLabel} · ` : ''}关闭后保留已填内容</Text></View>
            <Button className='secondary-button close-custom-exercise' disabled={saving || libraryBusy} onClick={close}>关闭</Button>
          </View>
          <ScrollView className='exercise-sheet-scroll' scrollY enhanced showScrollbar={false} bounces={false} scrollIntoView={sheet.scrollTarget}>
            <View className='custom-exercise-form'>
      <Text className='custom-help'>支持动作库中没有的新动作，仅自己可用。本轮使用组数、次数、重量和组间休息记录。</Text>
      {loading && <Text className='custom-help'>正在加载我的动作…</Text>}
      {options.length > 0 && <Picker disabled={frozen} range={options.map(item => item.exercise_name)} onChange={event => add(options[Number(event.detail.value)])}><View className='custom-select'>选择之前创建的动作 ›</View></Picker>}
      <View id={fieldId('name')} className='custom-sheet-field'><Text>动作名称</Text><Input disabled={frozen} adjustPosition={false} onFocus={() => sheet.focus(fieldId('name'))} className='custom-exercise-input custom-exercise-name' value={name} maxlength={100} placeholder='例如：弹力带单臂划船变式' onInput={e => setName(e.detail.value)} /></View>
      <View id={fieldId('description')} className='custom-sheet-field'><Text>动作方法</Text><Textarea disabled={frozen} adjustPosition={false} onFocus={() => sheet.focus(fieldId('description'))} className='custom-exercise-description' value={description} maxlength={2000} placeholder='描述姿势、动作过程和负重方式' onInput={e => setDescription(e.detail.value)} /></View>
      <View id={fieldId('muscles')} className='custom-sheet-field'><Text>训练部位（选填）</Text><Input disabled={frozen} adjustPosition={false} onFocus={() => sheet.focus(fieldId('muscles'))} className='custom-exercise-input custom-exercise-muscles' value={muscles} maxlength={300} placeholder='多个部位用逗号分隔' onInput={e => setMuscles(e.detail.value)} /></View>
      <Text>器械（选填）</Text><Picker disabled={frozen} range={equipmentOptions.map(x => x[0])} value={equipment} onChange={e => setEquipment(Number(e.detail.value))}><View className='custom-select'>{equipmentOptions[equipment][0]}</View></Picker>
      <Text>消耗估算分类（选填）</Text><Picker className='create-energy-picker' disabled={frozen} range={energyCategories.map(row => row.label)} value={energyIndex} onChange={e => { if (!busy.current) setEnergyIndex(Number(e.detail.value)) }}><View className='custom-select'>{energyCategories[energyIndex].label}</View></Picker>
      <Text className='custom-help'>{energyCategoryHelp} 未填写仍可创建、添加和记录。</Text>
      <View id={fieldId('contraindications')} className='custom-sheet-field'><Text>已知禁忌（选填）</Text><Input disabled={frozen} adjustPosition={false} onFocus={() => sheet.focus(fieldId('contraindications'))} className='custom-exercise-input custom-exercise-contraindications' value={contraindications} maxlength={500} placeholder='例如：肩关节；不知道可留空' onInput={e => setContraindications(e.detail.value)} /></View>
      <Text className='custom-safety-notice'>{CUSTOM_EXERCISE_NOTICE}</Text>
      {error && <Button className='secondary-button reload-custom-exercises' disabled={frozen || loading} onClick={loadOptions}>重新加载我的动作</Button>}
      <EnergyLibraryEditor options={options} onDraftChange={setLibraryDirty} onBusyChange={value => { libraryFlight.current = value; setLibraryBusy(value) }} onUpdated={option => setOptions(current => current.map(row => row.exercise_id === option.exercise_id ? option : row))} />
            </View>
          </ScrollView>
          <View className='exercise-sheet-footer' catchMove>
            {error && <View className='error-banner custom-sheet-error'>{error}</View>}
            <Button className='primary-button create-custom-exercise' disabled={frozen || disabled} onClick={create}>{saving ? '保存中…' : '创建并添加'}</Button>
          </View>
        </View>
      </View>
    </RootPortal>}
  </View>
}
