import { useEffect, useRef, useState } from 'react'
import { Input, Slider, Text, Textarea, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useLoad } from '@tarojs/taro'

import Button from '../../components/FeedbackButton'
import { useInteractionMotion } from '../../core/use-interaction-motion'
import { motionTiming } from '../../core/interaction-motion'

import { errorMessage } from '../../core/request'
import { workoutApi } from '../../services/workouts'
import { clearRest, endRest, newRest, readRest, recoverRest, restPayload, saveRest } from '../../core/workout-rest'
import type { RestJournal } from '../../core/workout-rest'
import type {
  SessionExercise,
  WorkoutAdjustment,
  WorkoutCompleteInput,
  WorkoutSession,
  WorkoutSetRecord
} from '../../types/api'
import './index.scss'

export default function ActiveWorkoutPage () {
  const [session, setSession] = useState<WorkoutSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [safetyReasons, setSafetyReasons] = useState<string[]>([])
  const [rest, setRest] = useState<RestJournal | null>(null)
  const [remaining, setRemaining] = useState(0)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const restNotified = useRef(false)
  const restRef = useRef<RestJournal | null>(null)
  const busy = useRef(false)
  const motion = useInteractionMotion()
  const [exitedRest, setExitedRest] = useState<RestJournal | null>(null)
  const restTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [activeSession, plans] = await Promise.all([
        workoutApi.active(),
        workoutApi.plans()
      ])
      setSession(activeSession)
      if (activeSession) {
        const restored = recoverRest(activeSession)
        restRef.current = restored
        setRest(restored)
      }
      const sourcePlan = activeSession?.plan_id
        ? plans.find(plan => plan.id === activeSession.plan_id)
        : null
      setSafetyReasons(
        sourcePlan?.safety_status === 'needs_review'
          ? sourcePlan.safety_reasons
          : []
      )
    } catch (requestError) {
      setError(errorMessage(requestError, '训练数据加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useLoad(() => {
    void load()
  })

  const syncRest = () => {
    if (!motion.isVisible()) return
    const currentRest = restRef.current
    if (!currentRest) return
    const nextRemaining = Math.max(0, Math.ceil((currentRest.endsAt - (currentRest.endedAt ?? Date.now())) / 1000))
    setRemaining(nextRemaining)
    if (nextRemaining === 0 && !restNotified.current) {
      restNotified.current = true
      void Taro.showToast({ title: '休息结束，可以开始下一组', icon: 'none', duration: 2400 })
    }
  }

  useDidShow(() => { motion.show(); syncRest() })
  useDidHide(() => {
    motion.hide()
    if (restTimer.current != null) clearInterval(restTimer.current)
    restTimer.current = null
  })

  useEffect(() => {
    restRef.current = rest
    if (!rest || !motion.visible) return undefined
    syncRest()
    const timer = setInterval(syncRest, 1000)
    restTimer.current = timer
    return () => {
      clearInterval(timer)
      if (restTimer.current === timer) restTimer.current = null
    }
  }, [rest, motion.visible])

  const persistRest = async (reason: 'next_set' | 'workout_ended') => {
    const current = restRef.current
    if (!current) return
    const token = motion.capture()
    const ended = endRest(current, reason)
    saveRest(ended)
    restRef.current = ended; setRest(ended)
    const updated = await workoutApi.recordRest(ended.sessionId, ended.exerciseId, ended.setNumber, restPayload(ended))
    clearRest(ended.sessionId)
    restRef.current = null; setRest(null); setSession(updated)
    if (reason === 'next_set' && motion.pulse('rest-exit', token, motionTiming.state)) setExitedRest(ended)
  }

  const finishRest = async () => {
    if (busy.current) return
    busy.current = true; setSaving(true); setError('')
    try { await persistRest('next_set') }
    catch (e) { setError(errorMessage(e, '休息记录保存失败，点击重试会保留原结束时刻')) }
    finally { busy.current = false; setSaving(false) }
  }

  const recordSet = async (
    exercise: SessionExercise,
    setNumber: number,
    reps: number,
    weightKg: number | null
  ) => {
    if (!session || busy.current) throw new Error('正在保存上一项，请稍后重试')
    const existing = setAt(exercise.sets_data, setNumber)
    const pending = readRest(session.id)
    if (!existing && restRef.current) {
      setError('请先结束休息，再开始下一组')
      throw new Error('请先结束休息，再开始下一组')
    }
    if (!existing && pending && (pending.exerciseId !== exercise.id || pending.setNumber !== setNumber)) {
      setError('上一组的保存结果尚未核对，请先重新读取训练或重试保存上一组')
      throw new Error('上一组的保存结果尚未核对')
    }
    busy.current = true
    setSaving(true)
    setError('')
    const token = motion.capture()
    motion.cancel('rest-exit')
    try {
      let journal: RestJournal | null = null
      if (!existing) {
        journal = pending?.exerciseId === exercise.id && pending.setNumber === setNumber ? pending : newRest(session.id, exercise, setNumber)
        saveRest(journal)
      }
      const updated = await workoutApi.recordSet(
        session.id,
        exercise.id,
        setNumber,
        reps,
        weightKg,
        journal ? new Date(journal.startedAt).toISOString() : undefined
      )
      setSession(updated)
      motion.pulse(`set:${exercise.id}:${setNumber}`, token)
      if (journal) {
        restNotified.current = false
        restRef.current = journal; setRest(journal)
        motion.pulse('rest-enter', token, motionTiming.enter)
      }
      const updatedExercise = updated.exercises.find(item => item.id === exercise.id)
      const savedSet = updatedExercise ? setAt(updatedExercise.sets_data, setNumber) : null
      if (savedSet && savedSet.is_personal_record && motion.isVisible() && token === motion.capture()) {
        void Taro.showToast({
          title: `🏆 新个人纪录：${performanceLabel(savedSet)}`,
          icon: 'none',
          duration: 2600
        }).catch(() => {})
      }
    } catch (requestError) {
      const message = errorMessage(requestError, '本组保存失败')
      setError(message)
      throw requestError
    } finally {
      setSaving(false)
      busy.current = false
    }
  }

  const complete = async () => {
    if (!session || session.total_sets < 1) return
    setError('')
    setFeedbackOpen(true)
  }

  const submitFeedback = async (feedback: WorkoutCompleteInput) => {
    if (!session || busy.current) return
    busy.current = true
    setSaving(true)
    setError('')
    try {
      await persistRest('workout_ended')
      const completed = await workoutApi.complete(session.id, feedback)
      setSession(completed)
      setFeedbackOpen(false)
    } catch (requestError) {
      setError(errorMessage(requestError, '无法完成训练'))
    } finally {
      setSaving(false)
      busy.current = false
    }
  }

  const abandon = async () => {
    if (!session) return
    const result = await Taro.showModal({
      title: '放弃本次训练？',
      content: '本次已经记录的组数会被删除，训练历史不会保留。',
      confirmText: '确认放弃',
      confirmColor: '#a13d31'
    })
    if (!result.confirm) return
    setSaving(true)
    try {
      await workoutApi.abandon(session.id)
      await Taro.navigateBack()
    } catch (requestError) {
      setError(errorMessage(requestError, '无法放弃训练'))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <View className='loading-state'>正在恢复训练…</View>

  if (!session) {
    return (
      <View className='page empty-workout'>
        <Text className='empty-icon'>○</Text>
        <Text className='empty-title'>当前没有进行中的训练</Text>
        {error && <View className='error-banner'>{error}</View>}
        <Button className='primary-button' onClick={() => Taro.navigateBack()}>返回训练计划</Button>
      </View>
    )
  }

  if (session.status === 'completed') {
    return <CompletionSummary session={session} />
  }

  if (session.status === 'ended_early') {
    return <View className='page'><View className='card early-end-card'>已提前结束，{session.total_sets} 组训练记录已保留。</View><Button className='primary-button ended-detail' onClick={() => Taro.redirectTo({ url: `/pages/workout-detail/index?id=${encodeURIComponent(session.id)}` })}>查看本次训练</Button></View>
  }

  const elapsedMinutes = Math.max(1, Math.floor((Date.now() - new Date(session.started_at).getTime()) / 60000))

  return (
    <View className={`page active-page ${motion.className}`}>
      <View className='session-card card'>
        <View>
          <Text className='session-kicker'>进行中 · {elapsedMinutes} 分钟</Text>
          <Text className='session-title'>{session.plan_name || '本次训练'}</Text>
          <Text className='session-meta'>
            {session.exercises.length} 个动作 · {session.total_sets} 组 · {Math.round(session.total_volume_kg)} kg
          </Text>
        </View>
        <View className='abandon-link' onClick={abandon}>放弃</View>
      </View>

      {error && <View className='error-banner'>{error}<Button className='secondary-button reload-workout' disabled={saving} onClick={load}>重新读取训练</Button></View>}
      {safetyReasons.length > 0 && (
        <View className='active-safety-warning'>
          <Text className='active-safety-title'>健康资料已变化，请停止训练并复核计划</Text>
          <Text className='active-safety-copy'>{safetyReasons.join('；')}。已经开始的训练不会被系统自动取消；如有疼痛、胸闷或明显不适，请立即停止并寻求专业意见。</Text>
        </View>
      )}

      {session.orphaned && <View className='active-safety-warning'>原计划已删除，已记录组数仍保留。可继续记录，并通过“完成训练”保存本次训练。没有记录时，可点击“放弃”退出。</View>}

      {session.exercises.map(exercise => (
        <ExerciseCard
          key={exercise.id}
          exercise={exercise}
          disabled={saving || Boolean(rest)}
          onSave={(setNumber, reps, weightKg) => recordSet(exercise, setNumber, reps, weightKg)}
          emphasizedSets={motion.enabled ? motion.active : []}
          onEdit={setNumber => motion.cancel(`set:${exercise.id}:${setNumber}`)}
        />
      ))}

      {rest && (
        <View className={`rest-bar ${remaining === 0 ? 'rest-finished' : ''} ${motion.has('rest-enter') ? 'rest-entering' : ''}`}>
          <View className='rest-main'>
            <View>
              <Text className='rest-time'>{rest.endedAt != null ? '休息已结束，待保存' : remaining === 0 ? '目标休息时间已到' : `组间休息 ${formatTime(remaining)}`}</Text>
              <Text className='rest-exercise'>{rest.exerciseName}</Text>
            </View>
            <View className='rest-actions'>
              <Button size='mini' disabled={saving || rest.endedAt != null} onClick={() => {
                restNotified.current = false
                const current = restRef.current
                if (!current) return
                const next = { ...current, endsAt: Math.max(current.endsAt, Date.now()) + 30000, totalSeconds: current.totalSeconds + 30 }
                saveRest(next); restRef.current = next; setRest(next)
              }}>+30秒</Button>
              <Button className='end-rest' size='mini' busy={saving} onClick={finishRest}>{rest.endedAt != null ? '重试保存休息' : '结束休息，开始下一组'}</Button>
            </View>
          </View>
          <View className='rest-track'>
            <View
              className='rest-progress'
              style={{ width: `${Math.min(100, Math.max(0, (1 - remaining / rest.totalSeconds) * 100))}%` }}
            />
          </View>
        </View>
      )}

      {!rest && exitedRest && motion.has('rest-exit') && (
        <View className='rest-exit-feedback'>
          <Text>休息已记录</Text>
          <Text className='rest-exercise'>{exitedRest.exerciseName}</Text>
        </View>
      )}

      {feedbackOpen && (
        <FeedbackPanel
          saving={saving}
          error={error}
          onCancel={() => setFeedbackOpen(false)}
          onSubmit={submitFeedback}
        />
      )}

      <View className='bottom-actions'>
        <Button
          className='primary-button complete-button'
          disabled={saving || session.total_sets < 1}
          onClick={complete}
        >
          {session.total_sets < 1 ? '至少完成一组' : `完成训练 · ${session.total_sets} 组`}
        </Button>
      </View>
    </View>
  )
}

function ExerciseCard ({
  exercise,
  disabled,
  onSave,
  emphasizedSets,
  onEdit
}: {
  exercise: SessionExercise
  disabled: boolean
  onSave: (setNumber: number, reps: number, weightKg: number | null) => Promise<void>
  emphasizedSets: string[]
  onEdit: (setNumber: number) => void
}) {
  const count = Math.max(1, exercise.target_sets || 1)
  return (
    <View className='exercise-card card'>
      <View className='exercise-heading'>
        <View>
          <Text className='exercise-name'>{exercise.exercise_name || '未命名动作'}</Text>
          {exercise.safety_notice && <Text className='custom-safety-notice'>{exercise.safety_notice}</Text>}
          <Text className='exercise-target'>
            目标 {count} 组 × {exercise.target_reps || '--'} 次 · 休息 {exercise.rest_seconds != null ? exercise.rest_seconds : 90} 秒
            {exercise.target_weight_kg != null ? ` · 建议 ${weightText(exercise.target_weight_kg)} kg` : ''}
          </Text>
        </View>
        {exercise.personal_best_reps != null && (
          <Text className='best-chip'>🏆 {bestLabel(exercise)}</Text>
        )}
      </View>
      <View className='set-table-heading'>
        <Text>组</Text><Text>重量 kg</Text><Text>次数</Text><Text>状态</Text>
      </View>
      {Array.from({ length: count }, (_, index) => {
        const setNumber = index + 1
        return (
          <SetEditor
            key={`${exercise.id}-${setNumber}`}
            setNumber={setNumber}
            existing={setAt(exercise.sets_data, setNumber)}
            previous={previousSetAt(exercise.previous_sets_data, setNumber)}
            targetWeight={exercise.target_weight_kg}
            targetReps={exercise.target_reps}
            disabled={disabled}
            onSave={onSave}
            emphasized={emphasizedSets.includes(`set:${exercise.id}:${setNumber}`)}
            onEdit={() => onEdit(setNumber)}
          />
        )
      })}
    </View>
  )
}

function SetEditor ({
  setNumber,
  existing,
  previous,
  targetWeight,
  targetReps,
  disabled,
  onSave,
  emphasized,
  onEdit
}: {
  setNumber: number
  existing: WorkoutSetRecord | null
  previous: WorkoutSetRecord | null
  targetWeight?: number | null
  targetReps?: string | null
  disabled: boolean
  onSave: (setNumber: number, reps: number, weightKg: number | null) => Promise<void>
  emphasized: boolean
  onEdit: () => void
}) {
  const initialWeight = existing && existing.weight_kg != null
    ? existing.weight_kg
    : targetWeight != null
      ? targetWeight
      : previous
        ? previous.weight_kg
        : null
  const [weight, setWeight] = useState(weightText(initialWeight))
  const [reps, setReps] = useState(
    (existing ? existing.reps.toString() : '') ||
    targetRepValue(targetReps) ||
    (previous ? previous.reps.toString() : '') ||
    ''
  )
  const [dirty, setDirty] = useState(false)
  const [rowSaving, setRowSaving] = useState(false)
  const rowBusy = useRef(false)
  const editRevision = useRef(0)

  useEffect(() => {
    if (!existing || dirty) return
    setWeight(weightText(existing.weight_kg))
    setReps(existing.reps.toString())
  }, [existing ? existing.reps : undefined, existing ? existing.weight_kg : undefined])

  const save = async () => {
    if (rowBusy.current || disabled) return
    const parsedReps = Number.parseInt(reps, 10)
    const parsedWeight = weight.trim() === '' ? null : Number.parseFloat(weight)
    if (!Number.isInteger(parsedReps) || parsedReps < 1 || (parsedWeight != null && (!Number.isFinite(parsedWeight) || parsedWeight < 0))) {
      await Taro.showToast({ title: '请填写有效的重量和次数', icon: 'none' })
      return
    }
    rowBusy.current = true
    const revision = editRevision.current
    setRowSaving(true)
    try {
      await onSave(setNumber, parsedReps, parsedWeight)
      if (editRevision.current === revision) setDirty(false)
      else onEdit()
    } catch (_) {
      // 页面级错误栏已显示请求错误，保留输入便于重试。
    } finally {
      setRowSaving(false)
      rowBusy.current = false
    }
  }

  const saved = Boolean(existing) && !dirty
  return (
    <View className={`set-row ${emphasized && saved && !rowSaving ? 'is-just-saved' : ''}`}>
      <Text className='set-number'>{setNumber}</Text>
      <Input
        className='set-input'
        type='digit'
        value={weight}
        placeholder='0'
        onInput={event => { editRevision.current++; setWeight(event.detail.value); setDirty(true); onEdit() }}
      />
      <Input
        className='set-input'
        type='number'
        value={reps}
        placeholder='次数'
        onInput={event => { editRevision.current++; setReps(event.detail.value); setDirty(true); onEdit() }}
      />
      <Button
        className={`set-save ${saved ? 'is-saved' : ''}`}
        size='mini'
        disabled={disabled}
        busy={rowSaving}
        onClick={save}
      >
        {existing && existing.is_personal_record && saved ? '🏆' : saved ? '已保存' : '保存'}
      </Button>
      {!existing && targetWeight != null
        ? <Text className='previous-hint'>系统建议 {weightText(targetWeight)} kg · 已按上次表现调整</Text>
        : previous && !existing && <Text className='previous-hint'>上次 {performanceLabel(previous)}</Text>}
    </View>
  )
}

function FeedbackPanel ({
  saving,
  error,
  onCancel,
  onSubmit
}: {
  saving: boolean
  error: string
  onCancel: () => void
  onSubmit: (feedback: WorkoutCompleteInput) => Promise<void>
}) {
  const [difficulty, setDifficulty] = useState<'too_easy' | 'just_right' | 'too_hard'>('just_right')
  const [rpe, setRpe] = useState(7)
  const [energy, setEnergy] = useState(3)
  const [pain, setPain] = useState(0)
  const [painAreas, setPainAreas] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const areas = ['膝关节', '肩关节', '腰背部', '踝关节', '腕肘部', '其他']

  const toggleArea = (area: string) => {
    setPainAreas(current => current.includes(area)
      ? current.filter(item => item !== area)
      : [...current, area])
  }

  const submit = async () => {
    if (pain > 0 && painAreas.length === 0) {
      await Taro.showToast({ title: '请选择不适部位', icon: 'none' })
      return
    }
    await onSubmit({
      difficulty_feedback: difficulty,
      perceived_exertion: rpe,
      energy_level: energy,
      pain_level: pain,
      pain_areas: painAreas,
      feedback_notes: notes.trim() || undefined
    })
  }

  return (
    <View className='feedback-overlay'>
      <View className='feedback-sheet'>
        <Text className='feedback-kicker'>完成前用 20 秒反馈</Text>
        <Text className='feedback-title'>这次训练感觉如何？</Text>
        {error && <View className='error-banner'>{error}</View>}

        <Text className='feedback-label'>整体难度</Text>
        <View className='feedback-options'>
          {([
            ['too_easy', '偏轻松'],
            ['just_right', '刚刚好'],
            ['too_hard', '偏吃力']
          ] as const).map(([value, label]) => (
            <View
              key={value}
              className={`feedback-option ${difficulty === value ? 'selected' : ''}`}
              onClick={() => setDifficulty(value)}
            >{label}</View>
          ))}
        </View>

        <View className='feedback-label-row'>
          <Text className='feedback-label'>主观用力程度 RPE</Text>
          <Text className='feedback-value'>{rpe} / 10</Text>
        </View>
        <Slider
          min={1}
          max={10}
          value={rpe}
          activeColor='#1d6b49'
          blockSize={22}
          onChange={event => setRpe(event.detail.value)}
        />

        <Text className='feedback-label'>今天的精力</Text>
        <View className='energy-options'>
          {[1, 2, 3, 4, 5].map(value => (
            <View
              key={value}
              className={`energy-option ${energy === value ? 'selected' : ''}`}
              onClick={() => setEnergy(value)}
            >{value}</View>
          ))}
        </View>

        <View className='feedback-label-row'>
          <Text className='feedback-label'>疼痛或异常不适</Text>
          <Text className={`feedback-value ${pain >= 4 ? 'danger' : ''}`}>{pain} / 10</Text>
        </View>
        <Slider
          min={0}
          max={10}
          value={pain}
          activeColor={pain >= 4 ? '#a13d31' : '#1d6b49'}
          blockSize={22}
          onChange={event => {
            setPain(event.detail.value)
            if (event.detail.value === 0) setPainAreas([])
          }}
        />
        {pain > 0 && (
          <View className='pain-area-options'>
            {areas.map(area => (
              <View
                key={area}
                className={`pain-area ${painAreas.includes(area) ? 'selected' : ''}`}
                onClick={() => toggleArea(area)}
              >{area}</View>
            ))}
          </View>
        )}
        {pain >= 4 && <View className='pain-warning'>明显疼痛将优先触发降量或安全换动作；持续不适请停止训练并咨询医生。</View>}

        <Textarea
          className='feedback-notes'
          value={notes}
          maxlength={1000}
          placeholder='其他感受（选填）'
          onInput={event => setNotes(event.detail.value)}
        />

        <View className='feedback-actions'>
          <Button className='secondary-button' disabled={saving} onClick={onCancel}>返回修改</Button>
          <Button className='primary-button' disabled={saving} onClick={submit}>
            {saving ? '正在保存训练…' : '完成训练'}
          </Button>
        </View>
      </View>
    </View>
  )
}

function CompletionSummary ({ session }: { session: WorkoutSession }) {
  const presentation = adaptivePresentation(session)
  const proposal = session.adaptive_adjustment_proposal
  return (
    <View className='page completion-page'>
      <View className='completion-hero'>
        <Text className='completion-mark'>✓</Text>
        <Text className='completion-kicker'>训练完成</Text>
        <Text className='completion-title'>{session.plan_name || '本次训练'}</Text>
        <Text className='completion-metrics'>{session.total_sets} 组 · {session.total_reps} 次 · {Math.round(session.total_volume_kg)} kg</Text>
      </View>

      <View className='card adjustment-summary'>
        <Text className='adjustment-title'>{presentation.title}</Text>
        <Text className='adjustment-subtitle'>{presentation.subtitle}</Text>
        {proposal && <Text className='adjustment-subtitle'>离开后可从训练历史找回这份调整提案。</Text>}
        {session.adjustments.map((item, index) => (
          <AdjustmentLine adjustment={item} key={`${item.exercise_id}-${index}`} />
        ))}
        {session.adjustments.length === 0 && (
          <Text className='no-adjustment'>训练记录和反馈已正常保存。</Text>
        )}
        {session.adaptive_adjustment_status === 'pending_confirmation' && proposal && (
          <Button
            className='primary-button adaptive-proposal-button'
            onClick={() => Taro.navigateTo({
              url: `/pages/plan-proposal-detail/index?id=${encodeURIComponent(proposal.id)}`
            })}
          >
            查看调整提案
          </Button>
        )}
      </View>

      <Button className='primary-button' onClick={() => Taro.reLaunch({ url: '/pages/workouts/index' })}>查看下一练</Button>
      <Button className='secondary-button completion-history' onClick={() => Taro.redirectTo({ url: '/pages/history/index' })}>查看训练历史</Button>
    </View>
  )
}

function adaptivePresentation (session: WorkoutSession): { title: string, subtitle: string } {
  const count = session.adjustments.length
  const suggestions = count ? `共 ${count} 项建议，计划尚未自动修改。` : ''
  return ({
    pending_confirmation: {
      title: '下一练调整待确认',
      subtitle: `已结合完成度和主观反馈生成建议；${suggestions}`
    },
    applied: {
      title: '下一练调整已应用',
      subtitle: `你已确认这份调整提案。${count ? `共应用 ${count} 项。` : ''}`
    },
    rejected: {
      title: '下一练调整已拒绝',
      subtitle: '训练记录已保留，原计划保持不变。'
    },
    expired: {
      title: '下一练调整已过期',
      subtitle: '训练记录已保留；如仍需调整，请重新发起。'
    },
    stale: {
      title: '下一练调整已失效',
      subtitle: '已有更新的训练结果或计划版本，本次旧建议不再可执行。'
    },
    failed: {
      title: '调整提案未生成',
      subtitle: '训练记录已保存，但本次调整建议无法安全生成，原计划保持不变。'
    },
    blocked_by_existing: {
      title: '已有计划提案待处理',
      subtitle: '本次训练已完成；为避免覆盖你的修改，请先处理现有计划提案。'
    },
    not_needed: {
      title: '当前目标保持不变',
      subtitle: '本次反馈未产生有效调整，原计划继续使用。'
    }
  } as const)[session.adaptive_adjustment_status] || {
    title: '当前目标保持不变',
    subtitle: '本次训练记录已保存。'
  }
}

function AdjustmentLine ({ adjustment }: { adjustment: WorkoutAdjustment }) {
  return (
    <View className={`adjustment-line ${adjustment.safety_priority ? 'safety' : ''}`}>
      <View className='adjustment-heading'>
        <Text className='adjustment-exercise'>{adjustment.exercise_name}</Text>
        <Text className='adjustment-action'>{adjustmentLabel(adjustment)}</Text>
      </View>
      <Text className='adjustment-reason'>{adjustment.reason}</Text>
    </View>
  )
}

function adjustmentLabel (adjustment: WorkoutAdjustment): string {
  const after = adjustment.after
  if (adjustment.action === 'replace_exercise') {
    return `换为 ${String(after.exercise_name || '安全动作')}`
  }
  const weight = typeof after.recommended_weight_kg === 'number'
    ? `${weightText(after.recommended_weight_kg)} kg`
    : ''
  const sets = typeof after.sets === 'number' ? `${after.sets} 组` : ''
  const reps = typeof after.reps === 'string' ? `${after.reps} 次` : ''
  return [weight, sets, reps].filter(Boolean).join(' · ')
}

function setAt (sets: WorkoutSetRecord[], setNumber: number): WorkoutSetRecord | null {
  return sets.find((item, index) => (
    item.set_number != null ? item.set_number : index + 1
  ) === setNumber) || null
}

function previousSetAt (sets: WorkoutSetRecord[], setNumber: number): WorkoutSetRecord | null {
  return setAt(sets, setNumber) || sets[sets.length - 1] || null
}

function performanceLabel (set: WorkoutSetRecord): string {
  return set.weight_kg == null ? `${set.reps} 次` : `${weightText(set.weight_kg)} kg × ${set.reps}`
}

function bestLabel (exercise: SessionExercise): string {
  return exercise.personal_best_weight_kg == null
    ? `${exercise.personal_best_reps} 次`
    : `${weightText(exercise.personal_best_weight_kg)} kg × ${exercise.personal_best_reps}`
}

function weightText (value?: number | null): string {
  if (value == null) return ''
  return Number.isInteger(value) ? value.toString() : value.toFixed(1)
}

function targetRepValue (value?: string | null): string {
  if (!value) return ''
  const matched = value.match(/\d+/)
  return matched ? matched[0] : ''
}

function formatTime (seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
