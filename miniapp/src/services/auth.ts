import Taro from '@tarojs/taro'

import { apiRequest } from '../core/request'
import { saveTokens } from '../core/storage'
import type { TokenResponse, WeChatLoginResponse } from '../types/api'

export type WeChatLoginStage = 'credential' | 'backend'

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), timeoutMs)
    promise.then(
      value => {
        clearTimeout(timer)
        resolve(value)
      },
      error => {
        clearTimeout(timer)
        reject(error)
      }
    )
  })
}

export async function authenticate(
  email: string,
  password: string
): Promise<void> {
  const tokens = await apiRequest<TokenResponse>('/auth/login', {
    method: 'POST',
    authenticated: false,
    data: { email, password }
  })
  saveTokens(tokens.access_token, tokens.refresh_token)
}

export async function authenticateWithWeChat(
  onStage?: (stage: WeChatLoginStage) => void
): Promise<WeChatLoginResponse> {
  if (onStage) onStage('credential')
  const loginResult = await withTimeout(
    Taro.login({ timeout: 10000 }),
    12000,
    '获取微信登录凭证超时，请检查手机网络后重试'
  )
  if (!loginResult.code) throw new Error('未能获取微信登录凭证，请重试')

  if (onStage) onStage('backend')
  const tokens = await apiRequest<WeChatLoginResponse>('/auth/wechat', {
    method: 'POST',
    authenticated: false,
    data: { code: loginResult.code }
  })
  saveTokens(tokens.access_token, tokens.refresh_token)
  return tokens
}

export function routeForProfile(onboardingCompleted: boolean): string {
  return onboardingCompleted
    ? '/pages/workouts/index'
    : '/pages/onboarding/index'
}
