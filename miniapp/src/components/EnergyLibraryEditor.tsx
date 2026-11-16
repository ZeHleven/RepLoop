import { useEffect, useRef, useState } from 'react'
import { Button, Picker, Text, View } from '@tarojs/components'
import { exerciseApi } from '../services/exercises'
import { errorMessage } from '../core/request'
import { energyCategories, energyCategoryLabel } from '../core/exercise-energy'
import type { EnergyCategory, PersonalizedExerciseOption } from '../types/api'
import './energy-classification.scss'

export default function EnergyLibraryEditor ({ options, onUpdated, onDraftChange, onBusyChange }: { options: PersonalizedExerciseOption[], onUpdated: (value: PersonalizedExerciseOption) => void, onDraftChange?: (dirty: boolean) => void, onBusyChange?: (busy: boolean) => void }) {
  const [selected, setSelected] = useState<PersonalizedExerciseOption | null>(null)
  const [category, setCategory] = useState<EnergyCategory | null>(null)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [message, setMessage] = useState('')
  const flight = useRef(false)
  const pending = useRef<{ id: string, energy_category: EnergyCategory | null, expected_version: number } | null>(null)
  const active = useRef(true)
  const listeners = useRef({ onDraftChange, onBusyChange })
  listeners.current = { onDraftChange, onBusyChange }
  const dirty = Boolean(uncertain || busy || (selected && category !== (selected.energy_category || null)))
  useEffect(() => { listeners.current.onDraftChange?.(dirty) }, [dirty])
  useEffect(() => { active.current = true; return () => { active.current = false; listeners.current.onDraftChange?.(false); listeners.current.onBusyChange?.(false) } }, [])
  const applied = (row: PersonalizedExerciseOption) => {
    pending.current = null; setUncertain(false); setSelected(row); setCategory(row.energy_category || null)
    setMessage('分类已保存，仅用于以后开始的训练。'); onUpdated(row)
  }
  const reconcile = async () => {
    const request = pending.current
    if (!request) return
    try {
      const list = await exerciseApi.custom()
      if (!active.current) return
      const row = list.find(item => item.exercise_id === request.id)
      if (!row) throw new Error('动作已不可选，暂无法核对保存结果；请重新加载后检查')
      if ((row.energy_category || null) === request.energy_category && row.energy_category_version === request.expected_version + 1) { applied(row); return }
      pending.current = null; setUncertain(false); setSelected(row); onUpdated(row)
      setMessage('已读取当前分类，选择仍保留。请核对原分类后再保存。')
    } catch (e) { if (active.current) { setUncertain(true); setMessage(errorMessage(e, '无法核对保存结果，恢复网络后请读取核对')) } }
  }
  const verify = async () => {
    if (flight.current) return
    flight.current = true; setBusy(true); listeners.current.onBusyChange?.(true)
    try { await reconcile() } finally { flight.current = false; if (active.current) { setBusy(false); listeners.current.onBusyChange?.(false) } }
  }
  const save = async () => {
    if (!selected || flight.current || pending.current) return
    const request = { id: selected.exercise_id, energy_category: category, expected_version: selected.energy_category_version ?? 0 }
    flight.current = true; pending.current = request; setBusy(true); setMessage(''); listeners.current.onBusyChange?.(true)
    try {
      const row = await exerciseApi.updateEnergyCategory(request.id, { energy_category: category, expected_version: request.expected_version })
      if (active.current) applied(row)
    } catch (e) { if (active.current) { setUncertain(true); setMessage(errorMessage(e, '正在读取核对保存结果')); await reconcile() } }
    finally { flight.current = false; if (active.current) { setBusy(false); listeners.current.onBusyChange?.(false) } }
  }
  if (!options.length) return null
  return <View className='energy-library-editor'>
    <Text className='custom-title'>修改估算分类</Text>
    <Text className='energy-help'>影响以后开始的训练，不修改已开始或已完成记录。这里只保存分类，不向计划添加动作。</Text>
    <Picker className='library-exercise-picker' disabled={busy || uncertain} range={['请选择已有动作', ...options.map(row => row.exercise_name)]}
      value={selected ? options.findIndex(row => row.exercise_id === selected.exercise_id) + 1 : 0}
      onChange={e => { if (flight.current || pending.current) return; const row = options[Number(e.detail.value) - 1] || null; setSelected(row); setCategory(row?.energy_category || null); setMessage('') }}>
      <View className='energy-category-select'>{selected?.exercise_name || '请选择已有动作'} ›</View>
    </Picker>
    {selected && <>
      <Text className='energy-help'>原分类：{energyCategoryLabel(selected.energy_category)}</Text>
      <Picker className='library-energy-picker' disabled={busy || uncertain} range={energyCategories.map(row => row.label)} value={energyCategories.findIndex(row => row.value === category)}
        onChange={e => { if (!flight.current && !pending.current) setCategory(energyCategories[Number(e.detail.value)]?.value || null) }}><View className='energy-category-select'>{energyCategoryLabel(category)} ›</View></Picker>
      <Button className='secondary-button save-library-energy' disabled={busy || uncertain} onClick={save}>{busy ? '正在保存／核对…' : '保存今后估算分类'}</Button>
    </>}
    {message && <View className='energy-save-message'>{message}</View>}
    {uncertain && <Button className='secondary-button verify-library-energy' disabled={busy} onClick={verify}>读取核对保存结果</Button>}
  </View>
}
