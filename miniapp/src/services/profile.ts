import { apiRequest } from '../core/request'
import type { ProfileUpdate, ProfileUpdateResult, UserProfile, WeightLog } from '../types/api'

export const profileApi = {
  get: () => apiRequest<UserProfile>('/profile'),
  update: (profile: ProfileUpdate) => apiRequest<ProfileUpdateResult>('/profile', {
    method: 'PUT',
    data: profile
  }),
  weightHistory: () => apiRequest<WeightLog[]>('/profile/weight'),
  logWeight: (weightKg: number) => apiRequest<WeightLog>('/profile/weight', {
    method: 'POST',
    data: { weight_kg: weightKg }
  })
}
