import { apiRequest } from '../core/request'
import type {
  PersonalizedPlanPreview,
  PersonalizedPlanPreviewRequest,
  WorkoutPlan,
  WorkoutCompleteInput,
  WorkoutProgress,
  WorkoutSession,
  SessionEnergyUpdate
} from '../types/api'

export const workoutApi = {
  plans: () => apiRequest<WorkoutPlan[]>('/workouts/plans'),
  generatePlan: () => apiRequest<WorkoutPlan>('/workouts/plans/generate', {
    method: 'POST',
    data: {}
  }),
  previewPersonalizedPlan: (data: PersonalizedPlanPreviewRequest = {}) => (
    apiRequest<PersonalizedPlanPreview>('/workouts/plans/personalized/preview', {
      method: 'POST',
      data
    })
  ),
  confirmPersonalizedPlan: (data: PersonalizedPlanPreview) => (
    apiRequest<WorkoutPlan>('/workouts/plans/personalized/confirm', {
      method: 'POST',
      data: {
        name: data.name,
        ...(data.training_days ? { training_days: data.training_days } : {}),
        goal: data.goal,
        duration_weeks: data.duration_weeks,
        days_per_week: data.days_per_week,
        session_duration_min: data.session_duration_min,
        rationale: data.rationale,
        safety_notes: data.safety_notes,
        exercises: data.exercises
      }
    })
  ),
  active: () => apiRequest<WorkoutSession | null>('/workouts/sessions/active'),
  detail: (id: string) => apiRequest<WorkoutSession>(`/workouts/sessions/${encodeURIComponent(id)}`),
  updateEnergyClassifications: (id: string, data: SessionEnergyUpdate) => apiRequest<WorkoutSession>(
    `/workouts/sessions/${encodeURIComponent(id)}/energy-classifications`, { method: 'PUT', data }
  ),
  removeArchived: (id: string) => apiRequest<void>(`/workouts/plans/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  finishEarly: (id: string) => apiRequest<WorkoutSession>(`/workouts/sessions/${encodeURIComponent(id)}/finish-early`, { method: 'POST' }),
  history: () => apiRequest<WorkoutSession[]>('/workouts/sessions'),
  progress: (weeks = 8, weekStart?: string) => apiRequest<WorkoutProgress>('/workouts/sessions/progress', {
    query: { weeks, ...(weekStart ? { week_start: weekStart } : {}) }
  }),
  recordRest: (sessionId: string, exerciseId: string, setNumber: number, data: { event_id: string, actual_rest_seconds: number, end_reason: 'next_set' | 'workout_ended' }) => apiRequest<WorkoutSession>(
    `/workouts/sessions/${sessionId}/exercises/${exerciseId}/sets/${setNumber}/rest`, { method: 'PUT', data }
  ),
  start: (planId: string, dayOfWeek: number) => apiRequest<WorkoutSession>(
    '/workouts/sessions/start',
    {
      method: 'POST',
      data: { plan_id: planId, day_of_week: dayOfWeek }
    }
  ),
  recordSet: (
    sessionId: string,
    sessionExerciseId: string,
    setNumber: number,
    reps: number,
    weightKg?: number | null,
    finishedAt?: string
  ) => apiRequest<WorkoutSession>(
    `/workouts/sessions/${sessionId}/exercises/${sessionExerciseId}/sets/${setNumber}`,
    {
      method: 'PUT',
      data: { reps, weight_kg: weightKg != null ? weightKg : null, ...(finishedAt ? { finished_at: finishedAt } : {}) }
    }
  ),
  complete: (sessionId: string, data: WorkoutCompleteInput) => apiRequest<WorkoutSession>(
    `/workouts/sessions/${sessionId}/complete`,
    { method: 'POST', data }
  ),
  abandon: (sessionId: string) => apiRequest<void>(
    `/workouts/sessions/${sessionId}`,
    { method: 'DELETE' }
  )
}
