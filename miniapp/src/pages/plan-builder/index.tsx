import { useRef, useState } from 'react'
import { Button, Input, Slider, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { nextTrainingDay } from '../../core/workout-schedule'
import { profileApi } from '../../services/profile'
import { workoutApi } from '../../services/workouts'
import CustomExerciseEntry from '../../components/CustomExerciseEntry'
import ExercisePicker from '../../components/ExercisePicker'
import PlanPageMeta from '../../components/PlanPageMeta'
import type { PersonalizedExerciseOption } from '../../types/api'
import type {
  PersonalizedPlanExercise,
  PersonalizedPlanPreview
} from '../../types/api'
import './index.scss'

const weekday = (day: number) => ['一', '二', '三', '四', '五', '六', '日'][day - 1] || day

export default function PlanBuilderPage () {
  const [preview, setPreview] = useState<PersonalizedPlanPreview | null>(null)
  const [trainingDays, setTrainingDays] = useState<number[]>([])
  const [generatedDays, setGeneratedDays] = useState<number[]>([])
  const [sessionDuration, setSessionDuration] = useState(45)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [savingMode, setSavingMode] = useState<'save' | 'start' | ''>('')
  const [error, setError] = useState('')
  const [sheetOpen, setSheetOpen] = useState(false)
  const busy = useRef(false)
  const daysPerWeek = trainingDays.length
  const needsRegeneration = Boolean(preview && (
    !sameDays(trainingDays, generatedDays) ||
    sessionDuration !== preview.session_duration_min
  ))
  const cannotSave = !preview || !daysPerWeek || needsRegeneration || generating || Boolean(savingMode)

  const generate = async (days: number, duration: number, initial = false, selectedDays?: number[]) => {
    if (busy.current) return
    if (!days) { setError('请至少选择一个训练日'); return }
    busy.current = true
    if (initial) setLoading(true)
    else setGenerating(true)
    setError('')
    try {
      const data = await workoutApi.previewPersonalizedPlan({
        days_per_week: days,
        ...(selectedDays ? { training_days: [...selectedDays] } : {}),
        session_duration_min: duration,
        duration_weeks: 4
      })
      const actualDays = previewDays(data)
      if (actualDays.length !== days || data.days_per_week !== days ||
          data.session_duration_min !== duration || actualDays.some(day => !Number.isInteger(day) || day < 1 || day > 7) ||
          (selectedDays && !sameDays(selectedDays, actualDays))) {
        throw new Error('返回的安排与所选训练日或时长不一致，请确认后端已更新并重试')
      }
      setPreview(data)
      setTrainingDays(actualDays)
      setGeneratedDays(actualDays)
      setSessionDuration(data.session_duration_min)
    } catch (requestError) {
      setError(errorMessage(requestError, '暂时无法生成训练计划'))
    } finally {
      setLoading(false)
      setGenerating(false)
      busy.current = false
    }
  }

  useLoad(() => {
    void (async () => {
      try {
        const profile = await profileApi.get()
        if (!profile.onboarding_completed) {
          await Taro.reLaunch({ url: '/pages/onboarding/index' })
          return
        }
        await generate(
          profile.training_days_per_week || 3,
          profile.session_duration_min || 45,
          true
        )
      } catch (requestError) {
        setError(errorMessage(requestError, '训练档案加载失败'))
        setLoading(false)
      }
    })()
  })

  const patchExercise = (index: number, patch: Partial<PersonalizedPlanExercise>) => {
    if (busy.current) return
    setPreview(current => current
      ? {
          ...current,
          exercises: current.exercises.map((item, itemIndex) => (
            itemIndex === index ? { ...item, ...patch } : item
          ))
        }
      : current)
  }

  const replaceExercise = async (index: number, optionIndex: number) => {
    if (!preview || busy.current) return
    const option = preview.exercise_options[optionIndex]
    const current = preview.exercises[index]
    if (!option || !current) return
    const duplicate = preview.exercises.some((item, itemIndex) => (
      itemIndex !== index &&
      item.day_of_week === current.day_of_week &&
      item.exercise_id === option.exercise_id
    ))
    if (duplicate) {
      await Taro.showToast({ title: '同一天已经安排了这个动作', icon: 'none' })
      return
    }
    patchExercise(index, {
      exercise_id: option.exercise_id,
      exercise_name: option.exercise_name,
      category: option.category,
      safety_notice: option.safety_notice
    })
  }

  const removeExercise = async (index: number) => {
    if (!preview || busy.current) return
    setPreview({
      ...preview,
      exercises: preview.exercises.filter((_, itemIndex) => itemIndex !== index)
    })
  }

  const addExercise = (day: number, option: PersonalizedExerciseOption) => {
    if (!preview || busy.current || !option) return
    if (preview.exercises.some(item => item.day_of_week === day && item.exercise_id === option.exercise_id)) { setError('同一天已经安排了这个动作'); return }
    if (preview.exercises.length >= 50) { setError('计划最多包含 50 项动作'); return }
    setPreview({ ...preview,
      exercise_options: preview.exercise_options.some(item => item.exercise_id === option.exercise_id) ? preview.exercise_options : [...preview.exercise_options, option],
      exercises: [...preview.exercises, { exercise_id: option.exercise_id, exercise_name: option.exercise_name, category: option.category, safety_notice: option.safety_notice, day_of_week: day, sets: 3, reps: '8-12', rest_seconds: 90, order_index: preview.exercises.filter(item => item.day_of_week === day).length }]
    })
    setError('')
  }

  const confirm = async (startImmediately: boolean) => {
    if (!preview || busy.current || sheetOpen) return
    if (!daysPerWeek || needsRegeneration) {
      setError('请先按所选训练日和时长重新编排，再保存计划')
      return
    }
    const actualDays = new Set(preview.exercises.map(item => item.day_of_week))
    if (actualDays.size !== preview.days_per_week) {
      setError('每个训练日至少需要保留一个动作')
      return
    }
    if (preview.exercises.some(item => !item.reps.trim())) {
      setError('请补全每个动作的目标次数')
      return
    }

    busy.current = true
    setSavingMode(startImmediately ? 'start' : 'save')
    setError('')
    const normalized = normalizeOrder(preview)
    try {
      const plan = await workoutApi.confirmPersonalizedPlan({ ...normalized, training_days: [...trainingDays] })
      if (!startImmediately) {
        await Taro.showToast({ title: '计划已保存', icon: 'success' })
        await Taro.reLaunch({ url: '/pages/workouts/index' })
        return
      }

      const recommendedDay = nextTrainingDay(
        plan.exercises.map(item => item.day_of_week)
      )
      const exercises = plan.exercises
        .filter(item => item.day_of_week === recommendedDay)
        .map(item => item.exercise_name || '未命名动作')
      const decision = await Taro.showModal({
        title: `推荐周${weekday(recommendedDay)}开始`,
        content: `本次动作：${exercises.join('、')}。现在开始，还是仅保存计划稍后训练？`,
        confirmText: '开始训练',
        cancelText: '仅保存'
      })
      if (!decision.confirm) {
        await Taro.showToast({ title: '计划已保存', icon: 'success' })
        await Taro.reLaunch({ url: '/pages/workouts/index' })
        return
      }
      try {
        await workoutApi.start(plan.id, recommendedDay)
        await Taro.redirectTo({ url: '/pages/workout-active/index' })
      } catch (startError) {
        await Taro.showModal({
          title: '计划已保存',
          content: errorMessage(startError, '暂时无法开始训练，可稍后从计划页启动。'),
          showCancel: false
        })
        await Taro.reLaunch({ url: '/pages/workouts/index' })
      }
    } catch (requestError) {
      setError(errorMessage(requestError, '计划保存失败，请检查后重试'))
    } finally {
      setSavingMode('')
      busy.current = false
    }
  }

  if (loading) return <View className='loading-state'><PlanPageMeta title='我的训练计划' /><Text>正在读取档案并编排训练…</Text></View>

  return (
    <View className='page plan-builder-page'>
      <PlanPageMeta title='我的训练计划' scrollLocked={sheetOpen} />
      <View className='builder-heading'>
        <Text className='builder-kicker'>你的首份个性化方案</Text>
        <Text className='builder-title'>{(preview && preview.name) || '训练计划'}</Text>
        <Text className='builder-subtitle'>先预览和调整，确认后才会保存。</Text>
      </View>

      {error && <View className='error-banner'>{error}</View>}

      <View className='card preference-card'>
        <View className='section-row'>
          <Text className='section-title'>训练节奏</Text>
          <Text className='section-value'>每周 {daysPerWeek} 天 · {sessionDuration} 分钟</Text>
        </View>
        <Text className='slider-label'>每周训练日（可多选）</Text>
        <View className='weekday-row'>
          {[1, 2, 3, 4, 5, 6, 7].map(day => (
            <Button
              key={day}
              className={`weekday ${trainingDays.includes(day) ? 'selected' : ''}`}
              disabled={generating || Boolean(savingMode)}
              onClick={() => {
                if (busy.current) return
                setTrainingDays(current => current.includes(day)
                  ? current.filter(value => value !== day)
                  : [...current, day].sort((left, right) => left - right))
                setError('')
              }}
            >{`周${weekday(day)}`}</Button>
          ))}
        </View>
        <Text className='schedule-hint'>选择你方便训练的星期，天数自动计算；至少选择一天。</Text>
        <Text className='slider-label'>单次训练时长</Text>
        <Slider
          className='session-duration-slider'
          min={20}
          max={120}
          step={5}
          value={sessionDuration}
          activeColor='#1d6b49'
          backgroundColor='#dfe8e0'
          blockSize={22}
          disabled={generating || Boolean(savingMode)}
          onChange={event => { if (!busy.current) setSessionDuration(event.detail.value) }}
        />
        {needsRegeneration && <Text className='schedule-pending'>训练日或时长已变更，下方仍是旧预览。请重新编排后再保存。</Text>}
        <Text className='schedule-hint'>重新编排会替换下方预览和手动调整，不会立即保存计划。</Text>
        <Button
          className='secondary-button regenerate-button'
          disabled={!daysPerWeek || generating || Boolean(savingMode)}
          onClick={() => generate(daysPerWeek, sessionDuration, false, trainingDays)}
        >
          {generating ? '正在重新编排…' : '按新节奏重新编排'}
        </Button>
      </View>

      {preview && (
        <>
          <View className='card rationale-card'>
            <Text className='section-title'>为什么这样安排</Text>
            {preview.rationale.map(item => <Text className='rationale-line' key={item}>• {item}</Text>)}
          </View>

          <Text className='plan-section-heading'>四周训练安排</Text>
          {generatedDays.map(day => (
            <View className='card day-card' key={day}>
              <View className='day-heading'>
                <View>
                  <Text className='day-title'>周{weekday(day)}训练</Text>
                  <Text className='day-meta'>{preview.exercises.filter(item => item.day_of_week === day).length} 个动作</Text>
                </View>
                <Text className='day-duration'>约 {preview.session_duration_min} 分钟</Text>
              </View>
              {preview.exercises.map((exercise, index) => exercise.day_of_week === day && (
                <View className='exercise-editor' key={`${day}-${exercise.exercise_id}-${index}`}>
                  <View className='exercise-topline'>
                    <View className='exercise-copy'>
                      <Text className='exercise-name'>{exercise.exercise_name}</Text>
                      <Text className='exercise-category'>{exercise.category}</Text>
                    </View>
                  </View>
                  {exercise.safety_notice && <Text className='custom-safety-notice'>{exercise.safety_notice}</Text>}
                  <View className='catalog-draft-actions'>
                    <ExercisePicker label='替换' dayLabel={`周${weekday(day)}`} currentId={exercise.exercise_id}
                      options={preview.exercise_options} selectedIds={preview.exercises.filter(item => item.day_of_week === day).map(item => item.exercise_id)}
                      disabled={Boolean(savingMode) || generating} onOpenChange={setSheetOpen}
                      onSelect={option => replaceExercise(index, preview.exercise_options.findIndex(item => item.exercise_id === option.exercise_id))} />
                    <Button className='catalog-delete' disabled={Boolean(savingMode) || generating} onClick={() => removeExercise(index)}>删除</Button>
                  </View>
                  {preview.exercise_options.find(option => option.exercise_id === exercise.exercise_id)?.counting_note && <Text className='catalog-counting-note'>{preview.exercise_options.find(option => option.exercise_id === exercise.exercise_id)?.counting_note}</Text>}

                  <View className='prescription-grid'>
                    <View className='prescription-field'>
                      <Text className='field-label'>组数</Text>
                      <View className='stepper'>
                        <View onClick={() => patchExercise(index, { sets: Math.max(1, exercise.sets - 1) })}>−</View>
                        <Text>{exercise.sets}</Text>
                        <View onClick={() => patchExercise(index, { sets: Math.min(8, exercise.sets + 1) })}>＋</View>
                      </View>
                    </View>
                    <View className='prescription-field'>
                      <Text className='field-label'>目标次数</Text>
                      <Input
                        className='reps-input'
                        value={exercise.reps}
                        maxlength={20}
                        onInput={event => patchExercise(index, { reps: event.detail.value })}
                      />
                    </View>
                    <View className='prescription-field'>
                      <Text className='field-label'>休息</Text>
                      <View className='stepper rest-stepper'>
                        <View onClick={() => patchExercise(index, { rest_seconds: Math.max(15, exercise.rest_seconds - 15) })}>−</View>
                        <Text>{exercise.rest_seconds}s</Text>
                        <View onClick={() => patchExercise(index, { rest_seconds: Math.min(600, exercise.rest_seconds + 15) })}>＋</View>
                      </View>
                    </View>
                  </View>
                </View>
              ))}
              {!preview.exercises.some(item => item.day_of_week === day) && <Text className='catalog-draft-empty'>本日还没有动作，点击下方添加。</Text>}
              <ExercisePicker label='＋ 添加动作' dayLabel={`周${weekday(day)}`} options={preview.exercise_options}
                selectedIds={preview.exercises.filter(item => item.day_of_week === day).map(item => item.exercise_id)}
                disabled={Boolean(savingMode) || generating || preview.exercises.length >= 50} onOpenChange={setSheetOpen}
                onSelect={option => addExercise(day, option)} />
              <CustomExerciseEntry dayLabel={`周${weekday(day)}`} onOpenChange={setSheetOpen} disabled={Boolean(savingMode) || generating} onAdd={option => addExercise(day, option)} />
            </View>
          ))}

          <View className='safety-card'>
            <Text className='safety-title'>训练安全提示</Text>
            {preview.safety_notes.map(item => <Text className='safety-line' key={item}>• {item}</Text>)}
          </View>

          <View className='builder-actions'>
            <Button
              className='secondary-button save-button'
              disabled={cannotSave}
              onClick={() => confirm(false)}
            >
              {savingMode === 'save' ? '保存中…' : '仅保存计划'}
            </Button>
            <Button
              className='primary-button start-now-button'
              disabled={cannotSave}
              onClick={() => confirm(true)}
            >
              {savingMode === 'start' ? '正在保存…' : '保存并开始推荐训练'}
            </Button>
          </View>
        </>
      )}
    </View>
  )
}

function previewDays (preview: PersonalizedPlanPreview): number[] {
  return [...new Set(preview.exercises.map(item => item.day_of_week))].sort((left, right) => left - right)
}

function sameDays (left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function normalizeOrder (preview: PersonalizedPlanPreview): PersonalizedPlanPreview {
  const orderByDay = new Map<number, number>()
  return {
    ...preview,
    exercises: preview.exercises.map(item => {
      const order = orderByDay.get(item.day_of_week) || 0
      orderByDay.set(item.day_of_week, order + 1)
      return { ...item, order_index: order }
    })
  }
}
