import { apiRequest } from '../core/request'
import type {
  DailyNutritionSummary,
  Food,
  MealItemInput,
  MealLog,
  CustomFoodValues
} from '../types/api'

export const nutritionApi = {
  foods: (query = '', limit = 20, scope: 'all' | 'mine' = 'all', category = '', offset = 0) => apiRequest<Food[]>('/foods/library', {
    query: { q: query || undefined, limit, scope, browse_category: category || undefined, offset }
  }),
  createFood: (data: CustomFoodValues & { client_request_id: string }) => apiRequest<Food>('/foods/custom', { method: 'POST', data }),
  foodCreationResult: (id: string) => apiRequest<Food>(`/foods/custom/requests/${encodeURIComponent(id)}`),
  updateFood: (id: string, data: CustomFoodValues & { version: number }) => apiRequest<Food>(`/foods/custom/${id}`, { method: 'PUT', data }),
  deleteFood: (id: string, version: number) => apiRequest<void>(`/foods/custom/${id}`, { method: 'DELETE', query: { version } }),
  today: () => apiRequest<DailyNutritionSummary>('/meals/today'),
  history: () => apiRequest<DailyNutritionSummary[]>('/meals/history'),
  logMeal: (data: {
    logged_at: string
    meal_type: MealLog['meal_type']
    items: MealItemInput[]
  }) => apiRequest<MealLog>('/meals', {
    method: 'POST',
    data
  }),
  updateMeal: (mealId: string, data: {
    logged_at: string
    meal_type: MealLog['meal_type']
    items: MealItemInput[]
  }) => apiRequest<MealLog>(`/meals/${mealId}`, {
    method: 'PUT',
    data
  }),
  deleteMeal: (mealId: string) => apiRequest<void>(`/meals/${mealId}`, {
    method: 'DELETE'
  })
}
