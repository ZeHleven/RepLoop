import { useEffect, useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { peekCached, readCached } from '../../core/read-cache'
import LoadingFeedback from '../../components/LoadingFeedback'

import { errorMessage } from '../../core/request'
import { historyExerciseSummary, proposalStatusLabel, workoutClock } from '../../core/workout-presentation'
import { workoutApi } from '../../services/workouts'
import type { WorkoutProgress, WorkoutSession } from '../../types/api'
import './index.scss'

export default function HistoryPage () {
  const [history, setHistory] = useState<WorkoutSession[]>(() =>
    (peekCached<WorkoutSession[]>('workout-history') || []).filter(item => item.status === 'completed' || item.status === 'ended_early'))
  const [progress, setProgress] = useState<WorkoutProgress | null>(() => peekCached<WorkoutProgress>('progress:8') || null)
  const [visible, setVisible] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [weekProgress, setWeekProgress] = useState<WorkoutProgress | null>(null)
  const [weekLoading, setWeekLoading] = useState(false)
  const weekGeneration = useRef(0)
  const selectedWeekRef = useRef<string | null>(null)
  const loadGeneration = useRef(0)

  const load = async (force = true) => {
    const generation = ++loadGeneration.current
    setLoading(true)
    setError('')
    try {
      const [historyData, progressData] = await Promise.all([
        readCached('workout-history', workoutApi.history, force),
        readCached('progress:8', workoutApi.progress, force)
      ])
      if (loadGeneration.current !== generation) return
      setHistory(historyData.filter(item => item.status === 'completed' || item.status === 'ended_early'))
      setProgress(progressData)
      if (selectedWeekRef.current) void selectWeek(selectedWeekRef.current, force)
    } catch (requestError) {
      if (loadGeneration.current === generation) setError(errorMessage(requestError, '训练历史加载失败，已显示的记录会保留'))
    } finally {
      if (loadGeneration.current === generation) setLoading(false)
    }
  }

  useDidShow(() => {
    setVisible(true); void load(false)
  })
  useDidHide(() => { setVisible(false); loadGeneration.current++; weekGeneration.current++ })
  useEffect(() => () => { loadGeneration.current++; weekGeneration.current++ }, [])

  const selectWeek = async (week: string, force = false) => {
    selectedWeekRef.current = week
    const ticket = ++weekGeneration.current
    setWeekLoading(true); setError('')
    try {
      const weeks = progress?.weeks || 8
      const data = await readCached(`progress:${weeks}:${week}`, () => workoutApi.progress(weeks, week), force)
      if (ticket === weekGeneration.current) setWeekProgress(data)
    } catch (e) { if (ticket === weekGeneration.current) setError(errorMessage(e, '当周数据加载失败，请重试')) }
    finally { if (ticket === weekGeneration.current) setWeekLoading(false) }
  }

  const showAllWeeks = () => { selectedWeekRef.current = null; weekGeneration.current++; setWeekProgress(null); setWeekLoading(false) }
  const metrics = weekProgress || progress
  const daily = weekProgress?.daily || []
  const shownHistory = weekProgress && daily.length > 0
    ? history.filter(item => item.trained_at >= daily[0].date && item.trained_at <= daily[daily.length - 1].date)
    : history

  const maxVolume = Math.max(1, ...(weekProgress ? daily.map(item => item.volume_kg) : progress ? progress.weekly.map(item => item.volume_kg) : [1]))

  return (
    <View className='page history-page'>
      <Text className='history-eyebrow'>长期进步来自每一次完成</Text>
      <Text className='history-title'>训练历史</Text>

      {error && <View className='error-banner'>{error}<Button className='secondary-button history-retry' onClick={() => load()}>重新加载</Button></View>}
      <LoadingFeedback loading={loading} hasContent={Boolean(progress)} visible={visible} text='正在整理训练记录…' refreshingText='正在更新训练记录…' />

      {progress && (
        <View className='card trend-card'>
          <Text className='trend-title'>{weekProgress ? `${shortDate(weekProgress.selected_week!)} 起的一周训练量` : `近 ${progress.weeks} 周训练量`}</Text>
          {weekProgress ? <Button className='secondary-button show-all-weeks' onClick={showAllWeeks}>返回近 {progress.weeks} 周</Button> : <Text className='trend-hint'>点击柱子查看当周每天的训练</Text>}
          {weekLoading && <Text className='trend-hint'>正在读取当周数据…</Text>}
          <View className='trend-chart'>
            {!weekProgress && progress.weekly.map(week => (
              <View className='week-column' key={week.week_start} onClick={() => selectWeek(week.week_start)}>
                <View className='bar-slot'>
                  <View
                    className='volume-bar'
                    style={{ height: `${Math.max(4, week.volume_kg / maxVolume * 100)}%` }}
                  />
                </View>
                <Text className='week-label'>{shortDate(week.week_start)}</Text>
              </View>
            ))}
            {weekProgress && daily.map((day, index) => <View className='week-column day-column' key={day.date}>
              <Text className='day-volume'>{Math.round(day.volume_kg)}</Text>
              <View className='bar-slot'><View className='volume-bar' style={{ height: `${Math.max(4, day.volume_kg / maxVolume * 100)}%` }} /></View>
              <Text className='week-label'>周{['一', '二', '三', '四', '五', '六', '日'][index]}</Text>
              <Text className='week-label'>{shortDate(day.date)}</Text>
            </View>)}
          </View>
          <View className='history-summary'>
            <Summary value={metrics!.total_sessions} label='次训练' />
            <Summary value={metrics!.total_sets} label='组' />
            <Summary value={Math.round(metrics!.total_volume_kg)} label='kg' />
          </View>
        </View>
      )}

      {!loading && !error && shownHistory.length === 0 && (
        <View className='card empty-state'>{weekProgress ? '本周还没有训练记录。' : '完成第一场训练后，记录和趋势会出现在这里。'}</View>
      )}

      {shownHistory.map(session => {
        const summary = historyExerciseSummary(session)
        const time = workoutClock(session.started_at)
        const records = session.exercises.reduce(
          (total, exercise) => total + exercise.sets_data.filter(set => set.is_personal_record).length,
          0
        )
        return (
          <View className='card history-card' key={session.id}>
            <View className='history-card-heading'>
              <View>
                <Text className='history-plan'>{session.plan_name || '自由训练'}</Text>
                <Text className='history-date'>{session.trained_at}{time ? ` · ${time}` : ''} · {session.status === 'ended_early' ? '提前结束' : '已完成'}</Text>
              </View>
              <View className='history-tags'>
                {records > 0 && <Text className='record-tag'>{records} 项新纪录</Text>}
              </View>
            </View>
            <View className='session-metrics'>
              <Text>{session.duration_min || 0} 分钟</Text>
              <Text>{session.total_sets} 组 · {session.total_reps} 次</Text>
              <Text>{Math.round(session.total_volume_kg)} kg</Text>
            </View>
            <View className='exercise-list'>
              {summary.shown.map(exercise => (
                <View className='exercise-line' key={exercise.id}>
                  <Text>{exercise.exercise_name || '未命名动作'}</Text>
                  <Text className='muted'>{exercise.sets_data.length} 组 · {Math.round(exercise.sets_data.reduce((sum, set) => sum + set.reps * (set.weight_kg || 0), 0))} kg</Text>
                </View>
              ))}
            </View>
            <Text className='history-summary-count'>{summary.label}</Text>
            {session.adjustments.length > 0 && !session.adaptive_adjustment_proposal && (
              <View className='history-adjustments'>
                <Text className='history-adjustment-title'>下一练建议 · {adaptiveStatusLabel(session)}</Text>
                {session.adjustments.slice(0, 3).map((item, index) => (
                  <Text className='history-adjustment-line' key={`${item.exercise_id}-${index}`}>
                    {item.exercise_name}：{item.reason}
                  </Text>
                ))}
              </View>
            )}
            <View className='history-card-actions'>
            <Button className='workout-detail-link' onClick={() => Taro.navigateTo({ url: `/pages/workout-detail/index?id=${encodeURIComponent(session.id)}` })}>查看本次训练</Button>
            {session.adaptive_adjustment_proposal && (
              <Button
                className={`adaptive-proposal-link ${session.adaptive_adjustment_status === 'pending_confirmation' ? 'proposal-pending' : ''}`}
                onClick={() => Taro.navigateTo({
                  url: `/pages/plan-proposal-detail/index?id=${encodeURIComponent(session.adaptive_adjustment_proposal!.id)}`
                })}
              >
                <Text>{session.adaptive_adjustment_status === 'pending_confirmation' ? '调整提案' : '提案结果'}</Text><Text className='history-proposal-status'>{proposalStatusLabel(session)}</Text>
              </Button>
            )}
            </View>
          </View>
        )
      })}
    </View>
  )
}

function Summary ({ value, label }: { value: number, label: string }) {
  return (
    <View>
      <Text className='summary-value'>{value}</Text>
      <Text className='summary-label'>{label}</Text>
    </View>
  )
}

function shortDate (value: string): string {
  const [, month, day] = value.split('-')
  return `${Number(month)}/${Number(day)}`
}

function adaptiveStatusLabel (session: WorkoutSession): string {
  return ({
    pending_confirmation: `待确认 ${session.adjustments.length} 项`,
    applied: `已应用 ${session.adjustments.length} 项`,
    rejected: '已拒绝',
    expired: '已过期',
    stale: '已失效',
    failed: '生成失败',
    blocked_by_existing: '被现有提案阻止',
    not_needed: '目标未变'
  } as const)[session.adaptive_adjustment_status] || '状态未知'
}
