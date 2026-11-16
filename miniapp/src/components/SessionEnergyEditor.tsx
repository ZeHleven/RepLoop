import { useEffect, useRef, useState } from 'react'
import { Button, Image, PageMeta, RootPortal, ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { workoutApi } from '../services/workouts'
import { errorMessage } from '../core/request'
import { classificationsApplied, energyCategories, energyCategoryLabel } from '../core/exercise-energy'
import WorkoutDisclosure from './WorkoutDisclosure'
import closeIcon from '../assets/icons/close-radix.svg'
import type { EnergyCategory, SessionEnergyUpdate, WorkoutSession } from '../types/api'
import './energy-classification.scss'

const categoryLabel = (value?: EnergyCategory | null) => energyCategoryLabel(value).replace(/^普通/, '')
const choices = energyCategories.filter(item => item.value !== null)
type ScopePhase = 'closed' | 'open' | 'closing'

export default function SessionEnergyEditor ({ session, onSaved, visible = true, layoutVersion = 0 }: { session: WorkoutSession, onSaved: (value: WorkoutSession) => void, visible?: boolean, layoutVersion?: number }) {
  const [draft, setDraft] = useState<Record<string, EnergyCategory | null>>({})
  const [scopePhase, setScopePhase] = useState<ScopePhase>('closed')
  const [savingFuture, setSavingFuture] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [message, setMessage] = useState('')
  const base = useRef(session)
  const active = useRef(true)
  const flight = useRef(false)
  const pending = useRef<SessionEnergyUpdate | null>(null)
  const phase = useRef<ScopePhase>('closed')
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clearClose = () => { if (closeTimer.current !== null) clearTimeout(closeTimer.current); closeTimer.current = null }
  const dismiss = (immediate = false, completed = false) => {
    if (!immediate && !completed && flight.current) return
    clearClose()
    if (immediate) { phase.current = 'closed'; setScopePhase('closed'); return }
    if (phase.current === 'closed') return
    phase.current = 'closing'; setScopePhase('closing')
    closeTimer.current = setTimeout(() => { closeTimer.current = null; phase.current = 'closed'; if (active.current) setScopePhase('closed') }, 160)
  }
  useEffect(() => { active.current = true; return () => { active.current = false; clearClose(); phase.current = 'closed' } }, [])
  useEffect(() => { if (!visible) dismiss(true) }, [visible])
  // Refreshes do not replace an in-progress edit or advance its expected versions.
  useEffect(() => { if (!Object.keys(draft).length && !pending.current) base.current = session }, [session])
  const rows = session.exercises.filter(row => row.energy_classification_editable)
  if (!rows.length) return null
  const changes = () => Object.entries(draft).filter(([id, category]) => category && base.current.exercises.some(row => row.id === id && row.energy_classification_editable))
  const inactiveNames = changes().filter(([id]) => !base.current.exercises.find(row => row.id === id)?.library_energy_editable)
    .map(([id]) => base.current.exercises.find(row => row.id === id)?.exercise_name || '未命名动作')
  const openScope = () => {
    if (!visible || flight.current || pending.current) return
    if (!changes().length) { setMessage('请先为要修改的动作选择分类'); return }
    clearClose(); setMessage(''); phase.current = 'open'; setScopePhase('open')
    void Taro.hideKeyboard?.().catch(() => {})
  }
  const applied = (data: WorkoutSession) => {
    base.current = data; pending.current = null; setUncertain(false); setDraft({}); dismiss(false, true)
    setMessage('分类已保存。返回饮食页后会重新获取估算；异常计时仍需单独核对。'); onSaved(data)
  }
  const reconcile = async () => {
    const request = pending.current
    if (!request) return
    try {
      const latest = await workoutApi.detail(session.id)
      if (!active.current) return
      if (classificationsApplied(latest, request)) { applied(latest); return }
      base.current = latest; pending.current = null; setUncertain(false); onSaved(latest)
      setMessage('已读取当前结果，未确认本次保存生效或存在版本变化。选择仍保留，请核对原分类后再保存。')
    } catch (e) {
      if (active.current) { setUncertain(true); setMessage(errorMessage(e, '暂时无法核对保存结果，请恢复网络后读取核对；不会重复提交')) }
    }
  }
  const verify = async () => {
    if (flight.current) return
    flight.current = true; setBusy(true)
    try { await reconcile() } finally { flight.current = false; if (active.current) setBusy(false) }
  }
  const save = async (future: boolean) => {
    if (!visible || phase.current !== 'open' || flight.current || pending.current) return
    const selected = changes()
    if (!selected.length) { setMessage('请先为要修改的动作选择分类'); return }
    if (future && inactiveNames.length) {
      setMessage('所选动作中有已停用的动作，只能选择“仅当次”。'); return
    }
    const request: SessionEnergyUpdate = {
      expected_version: base.current.energy_classification_version ?? 0, update_future: future,
      exercises: selected.map(([id, category]) => ({
        session_exercise_id: id, energy_category: category!,
        ...(future ? { expected_exercise_version: base.current.exercises.find(row => row.id === id)?.library_energy_category_version ?? undefined } : {})
      }))
    }
    flight.current = true; pending.current = request; setBusy(true); setSavingFuture(future); setMessage('')
    try {
      const data = await workoutApi.updateEnergyClassifications(session.id, request)
      if (active.current) applied(data)
    } catch (e) {
      if (active.current) { setUncertain(true); setMessage(errorMessage(e, '保存响应未确认，正在读取核对')); await reconcile() }
    } finally { flight.current = false; if (active.current) setBusy(false) }
  }
  const select = (id: string, value: EnergyCategory | null) => {
    if (!flight.current && !pending.current && phase.current === 'closed') { setDraft({ ...draft, [id]: value }); setMessage('') }
  }
  return <>
  <WorkoutDisclosure title='补充 / 更正估算分类' summary={`${rows.length} 个动作`} className='energy-classification-editor' headerOnly
    visible={visible} layoutVersion={layoutVersion} contentKey={`${JSON.stringify(draft)}:${message}:${busy}:${uncertain}:${session.energy_classification_version}`}>
    <View className='session-energy-content'>
    {rows.map(row => <View className='energy-classification-row' key={row.id}>
      <Text className='session-energy-name'>{row.exercise_name || '未命名动作'}</Text>
      <Text className='energy-help'>本次原分类：{categoryLabel(row.energy_category)}</Text>
      {choices.map(item => {
        const checked = (draft[row.id] === undefined ? row.energy_category : draft[row.id]) === item.value
        return <Button key={item.value!} className={`session-energy-option ${item.value === 'resistance_training' ? 'session-energy-resistance' : 'session-energy-bodyweight'} ${checked ? 'is-selected' : ''}`}
          aria-checked={checked} ariaLabel={`${categoryLabel(item.value)}，${checked ? '已选择' : '未选择'}`} disabled={busy || uncertain || scopePhase !== 'closed'} onClick={() => select(row.id, item.value)}>
          <Text>{categoryLabel(item.value)}</Text>{checked && <Text className='session-energy-check' aria-hidden>✓</Text>}
        </Button>
      })}
    </View>)}
    <Text className='energy-help session-energy-safety'>仅用于整场训练的消耗粗估，不代表健康适用性认证。不适用于跑步、骑行或 HIIT。</Text>
    {message && <View className='energy-save-message'>{message}</View>}
    {uncertain && <Button className='secondary-button verify-session-energy' disabled={busy} onClick={verify}>读取核对保存结果</Button>}
    <Button className='primary-button save-session-energy' disabled={busy || uncertain || !Object.values(draft).some(Boolean)} onClick={openScope}>{busy ? '正在保存／核对…' : '保存分类并重新估算'}</Button>
    </View>
  </WorkoutDisclosure>
  {visible && scopePhase !== 'closed' && <>
    <PageMeta pageStyle='overflow:hidden' />
    <RootPortal><View className={`energy-scope-layer energy-scope-${scopePhase}`}>
      <View className='energy-scope-backdrop' catchMove onClick={() => dismiss()} />
      <View className='energy-scope-position' catchMove>
        <View className='energy-scope-panel' ariaRole='dialog' ariaLabel='是否同步到这些动作以后的训练？'>
          <ScrollView scrollY className='energy-scope-scroll' enhanced showScrollbar={false} bounces={false}>
            <View className='energy-scope-inner'>
              <Button className='energy-scope-close' disabled={busy} ariaLabel='关闭，不保存' onClick={() => dismiss()}><Image className='energy-scope-close-icon' src={closeIcon} mode='scaleToFill' /></Button>
              <Text className='energy-scope-eyebrow'>保存范围</Text>
              <Text className='energy-scope-title'>是否同步到这些动作{`\n`}以后的训练？</Text>
              <Text className='energy-scope-description'>本次训练会保存所选分类；{`\n`}其他历史记录保持不变。</Text>
              <Text className='energy-help energy-scope-help'>选择“同步”，还会更新这些动作今后开始训练时的分类。</Text>
              {inactiveNames.length > 0 && <View className='energy-scope-inactive'>“{inactiveNames.join('、')}”已停用，本次只能选择“仅当次”。</View>}
              {message && <View className='energy-save-message'>{message}</View>}
              {uncertain && <Button className='secondary-button energy-scope-verify' disabled={busy} onClick={verify}>读取核对保存结果</Button>}
              <View className='energy-scope-actions'>
                <Button className='secondary-button energy-scope-future' disabled={busy || uncertain || inactiveNames.length > 0} onClick={() => save(true)}>{busy && savingFuture ? '正在保存…' : '同步'}</Button>
                <Button className='primary-button energy-scope-current' disabled={busy || uncertain} onClick={() => save(false)}>{busy && !savingFuture ? '正在保存…' : '仅当次'}</Button>
              </View>
              <Text className='energy-scope-footnote'>{busy ? '正在保存／核对，请稍候。' : '不确定时，可以先仅保存到本次。'}</Text>
            </View>
          </ScrollView>
        </View>
      </View>
    </View></RootPortal>
  </>}
  </>
}
