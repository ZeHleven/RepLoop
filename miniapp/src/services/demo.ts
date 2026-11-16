import Taro from '@tarojs/taro'
import { apiRequest, ApiRequestError } from '../core/request'
import { clearTokens, saveTokens } from '../core/storage'

export const DEMO_MODE = typeof __FITNESS_DEMO_MODE__ !== 'undefined' && __FITNESS_DEMO_MODE__
const REQUEST_KEY = 'fitness_demo_request_key'

export interface DemoSession {
  expires_at: string
  scenario_name: string
  scenario_description: string
  quick_prompts: string[]
  resets_remaining: number
  model_available: boolean
}
interface DemoCredentials extends DemoSession {
  access_token: string
  refresh_token: string
}

export function demoRequestKey (): string {
  let key = Taro.getStorageSync<string>(REQUEST_KEY)
  if (!/^[0-9a-f]{64}$/.test(key || '')) {
    const bytes = new Uint8Array(32)
    // H5 demo requires HTTPS (localhost is also a secure context).
    globalThis.crypto.getRandomValues(bytes)
    key = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
    Taro.setStorageSync(REQUEST_KEY, key)
  }
  return key
}

function accept (session: DemoCredentials): DemoSession {
  clearTokens()
  saveTokens(session.access_token, session.refresh_token)
  return session
}

export const demoApi = {
  current: () => apiRequest<DemoSession>('/demo/session'),
  start: async (): Promise<DemoSession> => {
    const create = () => apiRequest<DemoCredentials>('/demo/sessions', {
      method: 'POST', authenticated: false, timeout: 30000,
      data: { request_key: demoRequestKey() }
    })
    try { return accept(await create()) }
    catch (error) {
      if (!(error instanceof ApiRequestError) || error.statusCode !== 401) throw error
      Taro.removeStorageSync(REQUEST_KEY)
      return accept(await create())
    }
  },
  reset: async (): Promise<DemoSession> => accept(await apiRequest<DemoCredentials>(
    '/demo/reset', { method: 'POST', timeout: 30000 }
  ))
}
