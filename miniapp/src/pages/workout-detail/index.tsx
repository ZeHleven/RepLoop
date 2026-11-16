import { useEffect, useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useLoad } from '@tarojs/taro'
import { peekCached, readCached } from '../../core/read-cache'
import LoadingFeedback from '../../components/LoadingFeedback'
import { workoutApi } from '../../services/workouts'
import { errorMessage } from '../../core/request'
import { inlineSetRest, plannedExerciseSummary, proposalStatusLabel } from '../../core/workout-presentation'
import type { WorkoutSession } from '../../types/api'
import SessionEnergyEditor from '../../components/SessionEnergyEditor'
import WorkoutDisclosure from '../../components/WorkoutDisclosure'
import './index.scss'

export default function WorkoutDetailPage () {
  const [session, setSession] = useState<WorkoutSession | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const id = useRef('')
  const generation = useRef(0)
  const mounted = useRef(true)
  const [visible, setVisible] = useState(true)
  const [layoutVersion, setLayoutVersion] = useState(0)
  useDidShow(() => { setVisible(true); if (id.current) void load(false) })
  useDidHide(() => { setVisible(false); generation.current++ })
  useEffect(() => {
    mounted.current = true
    const resize = () => setLayoutVersion(value => value + 1)
    Taro.onWindowResize?.(resize)
    return () => { mounted.current = false; generation.current++; Taro.offWindowResize?.(resize) }
  }, [])
  const load = async (force = true) => {
    const ticket = ++generation.current
    if (!id.current) { setError('缺少训练记录标识，请从训练历史重新打开'); return }
    const cached = peekCached<WorkoutSession>(`workout-detail:${id.current}`)
    if (cached && !session) setSession(cached)
    setLoading(true); setError('')
    try {
      const sessionId = id.current
      const data = await readCached(`workout-detail:${sessionId}`, () => workoutApi.detail(sessionId), force)
      if (mounted.current && ticket === generation.current) setSession(data)
    } catch (e) { if (mounted.current && ticket === generation.current) setError(errorMessage(e, '训练记录加载失败')) }
    finally { if (mounted.current && ticket === generation.current) setLoading(false) }
  }
  useLoad(options => { id.current = options.id || ''; void load(false) })
  const trained = session?.exercises.filter(exercise => exercise.sets_data.length > 0) || []
  const untrained = session?.exercises.filter(exercise => exercise.sets_data.length === 0) || []
  return <View className='page workout-detail-page'>
    {error && <View className='error-banner'>{error}<Button className='secondary-button detail-retry' onClick={() => load()}>重试</Button></View>}
    <LoadingFeedback loading={loading} hasContent={Boolean(session)} visible={visible} text='正在加载本次训练…' refreshingText='正在更新本次训练…' />
    {session && <>
      <View className='card detail-heading'>
        <Text className='detail-state'>{session.status === 'completed' ? '已完成' : session.status === 'ended_early' ? '提前结束 · 已保留记录' : '进行中'}</Text>
        <Text className='page-title'>{session.plan_name || '本次训练'}</Text>
        <Text>{session.trained_at} · {session.duration_min || 0} 分钟</Text>
        <Text>{session.total_sets} 组 · {session.total_reps} 次 · {session.total_volume_kg} kg 训练量</Text>
      </View>
      {trained.map(exercise => <WorkoutDisclosure className='detail-exercise' key={`${session.id}:${exercise.id}`}
        title={exercise.exercise_name || '未命名动作'} summary={plannedExerciseSummary(exercise)}
        visible={visible} layoutVersion={layoutVersion} contentKey={exercise}>
        <Text className='detail-plan'>计划{plannedExerciseSummary(exercise)} · 组间歇{exercise.rest_seconds ?? '—'}秒</Text>
        <Text className='detail-actual'>实际记录 {exercise.sets_data.length} 组</Text>
        {exercise.safety_notice && <Text className='detail-note'>{exercise.safety_notice}</Text>}
        {exercise.sets_data.map((set, index) => <View className='detail-set' key={set.set_number || index + 1}>
          <Text className='detail-set-line'>第{set.set_number || index + 1}组 · {set.reps}次 · {set.weight_kg == null ? '未记录重量' : `${set.weight_kg}kg`} · <Text className='detail-rest'>{inlineSetRest(set)}</Text></Text>
        </View>)}
      </WorkoutDisclosure>)}
      {untrained.length > 0 && <WorkoutDisclosure key={`${session.id}:untrained`} className='untrained-region' title='未训练动作' summary={`${untrained.length}项`}
        visible={visible} layoutVersion={layoutVersion} contentKey={session}>
        {untrained.map(exercise => <View className='untrained-item' key={exercise.id}>
          <Text className='untrained-name'>{exercise.exercise_name || '未命名动作'}</Text>
          <Text className='detail-plan'>计划{plannedExerciseSummary(exercise)} · 组间歇{exercise.rest_seconds ?? '—'}秒</Text>
          <Text className='detail-note'>未记录训练组</Text>
          {exercise.safety_notice && <Text className='detail-note'>{exercise.safety_notice}</Text>}
        </View>)}
      </WorkoutDisclosure>}
      <SessionEnergyEditor key={session.id} session={session} onSaved={setSession} visible={visible} layoutVersion={layoutVersion} />
      {(session.feedback?.feedback_notes || session.notes) && <View className='card detail-notes'>{session.feedback?.feedback_notes && <Text>训练反馈：{session.feedback.feedback_notes}</Text>}{session.notes && <Text>备注：{session.notes}</Text>}</View>}
      {session.adaptive_adjustment_proposal && <Button className='secondary-button detail-proposal' onClick={() => Taro.navigateTo({ url: `/pages/plan-proposal-detail/index?id=${encodeURIComponent(session.adaptive_adjustment_proposal!.id)}` })}><Text>查看调整提案</Text><Text className='detail-proposal-status'>{proposalStatusLabel(session)}</Text></Button>}
    </>}
  </View>
}
