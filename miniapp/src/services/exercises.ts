import { apiRequest } from '../core/request'
import type { EnergyCategory, PersonalizedExerciseOption } from '../types/api'

export interface CustomExerciseInput {
  energy_category?: EnergyCategory | null
  name: string
  description: string
  muscles: string[]
  equipment: string[]
  contraindications: string[]
}

export const exerciseApi = {
  custom: () => apiRequest<PersonalizedExerciseOption[]>('/exercises/custom'),
  createCustom: (data: CustomExerciseInput) => apiRequest<PersonalizedExerciseOption>('/exercises/custom', { method: 'POST', data }),
  updateEnergyCategory: (id: string, data: { energy_category: EnergyCategory | null, expected_version: number }) => apiRequest<PersonalizedExerciseOption>(
    `/exercises/custom/${encodeURIComponent(id)}/energy-category`, { method: 'PUT', data }
  )
}
