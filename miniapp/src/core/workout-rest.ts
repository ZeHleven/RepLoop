import Taro from '@tarojs/taro'
import type { SessionExercise, WorkoutSession } from '../types/api'

export interface RestJournal {
  sessionId: string
  exerciseId: string
  setNumber: number
  exerciseName: string
  eventId: string
  startedAt: number
  endsAt: number
  totalSeconds: number
  endedAt?: number
  endReason?: 'next_set' | 'workout_ended'
}
const key = (sessionId: string) => `fitness_workout_rest_v1:${sessionId}`
export function saveRest (value: RestJournal) { Taro.setStorageSync(key(value.sessionId), value) }
export function clearRest (sessionId: string) { Taro.removeStorageSync(key(sessionId)) }
export function readRest (sessionId: string): RestJournal | null {
  const value = Taro.getStorageSync<RestJournal>(key(sessionId))
  return value && value.sessionId === sessionId && typeof value.exerciseId === 'string' && Number.isInteger(value.setNumber) && Number.isFinite(value.startedAt) && Number.isFinite(value.endsAt) && value.totalSeconds > 0 ? value : null
}
export function newRest (sessionId: string, exercise: SessionExercise, setNumber: number, now = Date.now()): RestJournal {
  const seconds = exercise.rest_seconds ?? 90
  return { sessionId, exerciseId: exercise.id, setNumber, exerciseName: exercise.exercise_name || '当前动作', eventId: `rest-${exercise.id}-${setNumber}`, startedAt: now, endsAt: now + seconds * 1000, totalSeconds: Math.max(1, seconds) }
}
export function endRest (value: RestJournal, reason: 'next_set' | 'workout_ended', now = Date.now()): RestJournal {
  return value.endedAt != null ? value : { ...value, endedAt: Math.max(value.startedAt, now), endReason: reason }
}
export function restPayload (value: RestJournal) {
  if (value.endedAt == null || !value.endReason) throw new Error('休息尚未结束')
  const seconds = Math.round((value.endedAt - value.startedAt) / 1000)
  if (!Number.isFinite(seconds) || seconds < 0 || seconds > 86400) throw new Error('计时超出一天，无法作为可靠组间休息；请保留记录并结束旧训练')
  return { event_id: value.eventId, actual_rest_seconds: seconds, end_reason: value.endReason }
}
export function recoverRest (session: WorkoutSession): RestJournal | null {
  if (session.status !== 'in_progress') { clearRest(session.id); return null }
  const local = readRest(session.id)
  if (local) {
    const set = session.exercises.find(item => item.id === local.exerciseId)?.sets_data.find(item => item.set_number === local.setNumber)
    if (!set) return null
    if (set.rest_event_id) { clearRest(session.id); return null }
    return local
  }
  const candidates = session.exercises.flatMap(exercise => exercise.sets_data
    .filter(set => set.rest_started_at && !set.rest_event_id && set.set_number)
    .map(set => ({ exercise, set, started: Date.parse(set.rest_started_at!) })))
    .filter(item => Number.isFinite(item.started))
    .sort((a, b) => b.started - a.started)
  const latest = candidates[0]
  if (!latest) return null
  const journal = newRest(session.id, latest.exercise, latest.set.set_number!, latest.started)
  saveRest(journal)
  return journal
}
