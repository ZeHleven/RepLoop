import Taro from '@tarojs/taro'
import { invalidateReadCache, readCacheSession } from './read-cache'

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  saveTokens
} from './storage'
import type { TokenResponse } from '../types/api'

const API_BASE_URL = (
  __FITNESS_API_BASE_URL__ || 'http://127.0.0.1:8000/api/v1'
).replace(/\/$/, '')
const CLOUD_ENV = __FITNESS_CLOUD_ENV__
const CLOUD_SERVICE = __FITNESS_CLOUD_SERVICE__
const USE_CLOUD_TRANSPORT = Boolean(CLOUD_ENV && CLOUD_SERVICE)
const CLOUD_API_PREFIX = '/api/v1'
const REQUEST_TIMEOUT_MS = 15000

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE'

interface RequestOptions {
  method?: HttpMethod
  data?: unknown
  query?: Record<string, string | number | boolean | undefined>
  authenticated?: boolean
  timeout?: number
}

export interface ApiErrorPayload {
  code?: unknown
  message?: unknown
  detail?: unknown
}

export class ApiRequestError extends Error {
  readonly statusCode: number
  readonly code?: string
  readonly payload: ApiErrorPayload

  constructor (
    message: string,
    statusCode: number,
    payload: ApiErrorPayload
  ) {
    super(message)
    this.name = 'ApiRequestError'
    this.statusCode = statusCode
    const nested = payload.detail && typeof payload.detail === 'object'
      ? payload.detail as ApiErrorPayload
      : null
    this.code = typeof payload.code === 'string'
      ? payload.code
      : typeof nested?.code === 'string' ? nested.code : undefined
    this.payload = payload
  }
}

interface TransportResponse<T> {
  data: T
  statusCode: number
  header?: Record<string, string>
  errMsg?: string
}

interface CloudContainerOptions {
  config: { env: string }
  path: string
  method: HttpMethod
  data?: unknown
  header: Record<string, string>
  timeout: number
}

interface CloudContainerClient {
  init(config: { env: string; traceUser?: boolean }): void
  callContainer<T>(options: CloudContainerOptions): Promise<TransportResponse<T>>
}

let refreshPromise: Promise<string> | null = null
let refreshSession = -1
let cloudInitialized = false

function withQuery(path: string, query?: RequestOptions['query']): string {
  if (!query) return path
  const values = Object.entries(query)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  return values.length ? `${path}?${values.join('&')}` : path
}

function cloudClient(): CloudContainerClient {
  return Taro.cloud as unknown as CloudContainerClient
}

export function initializeCloudTransport(): void {
  if (!USE_CLOUD_TRANSPORT || cloudInitialized) return

  const client = cloudClient()
  if (!client || typeof client.callContainer !== 'function') {
    throw new Error('当前微信基础库不支持云托管，请升级微信后重试')
  }
  client.init({ env: CLOUD_ENV, traceUser: true })
  cloudInitialized = true
}

async function transportRequest<T>(
  path: string,
  method: HttpMethod,
  data: unknown,
  header: Record<string, string>,
  timeout = REQUEST_TIMEOUT_MS
): Promise<TransportResponse<T>> {
  if (USE_CLOUD_TRANSPORT) {
    initializeCloudTransport()
    return cloudClient().callContainer<T>({
      config: { env: CLOUD_ENV },
      path: `${CLOUD_API_PREFIX}${path}`,
      method,
      data,
      header: {
        ...header,
        'X-WX-SERVICE': CLOUD_SERVICE
      },
      timeout
    })
  }

  return Taro.request<T>({
    url: `${API_BASE_URL}${path}`,
    method,
    data,
    header,
    timeout
  })
}

async function refreshAccessToken(): Promise<string> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) throw new Error('登录状态已失效，请重新登录')

  const response = await transportRequest<TokenResponse | ApiErrorPayload>(
    '/auth/refresh',
    'POST',
    { refresh_token: refreshToken },
    { 'Content-Type': 'application/json' }
  )
  if (getRefreshToken() !== refreshToken) {
    throw new Error('登录状态已改变，请在当前账号下重新操作')
  }

  if (response.statusCode < 200 || response.statusCode >= 300) {
    const payload = response.data as ApiErrorPayload
    throw new ApiRequestError(
      response.statusCode === 401
        ? '登录状态已失效，请重新登录'
        : '暂时无法续期登录，请检查网络后重试。登录信息已保留',
      response.statusCode,
      payload
    )
  }

  const tokens = response.data as TokenResponse
  if (!tokens || typeof tokens.access_token !== 'string' || !tokens.access_token ||
      typeof tokens.refresh_token !== 'string' || !tokens.refresh_token) {
    throw new Error('登录续期响应无效，请稍后重试。登录信息已保留')
  }
  if (getRefreshToken() !== refreshToken) {
    throw new Error('登录状态已改变，请在当前账号下重新操作')
  }
  saveTokens(tokens.access_token, tokens.refresh_token, true)
  return tokens.access_token
}

async function getFreshAccessToken(): Promise<string> {
  if (!refreshPromise || refreshSession !== readCacheSession()) {
    refreshSession = readCacheSession()
    const current = refreshAccessToken().finally(() => {
      if (refreshPromise === current) refreshPromise = null
    })
    refreshPromise = current
  }
  return refreshPromise
}

async function requestWithAuth<T>(
  path: string,
  options: RequestOptions = {},
  allowRefresh = true
): Promise<T> {
  const authenticated = options.authenticated !== false
  const account = readCacheSession()
  const accessToken = authenticated ? getAccessToken() : ''
  const requestRefreshToken = authenticated ? getRefreshToken() : ''
  const response = await transportRequest<T | ApiErrorPayload>(
    withQuery(path, options.query),
    options.method || 'GET',
    options.data,
    {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {})
    },
    options.timeout
  )
  if (authenticated && account !== readCacheSession()) {
    throw new Error('登录状态已改变，请在当前账号下重新操作')
  }

  if (
    response.statusCode === 401 &&
    authenticated &&
    allowRefresh &&
    getRefreshToken()
  ) {
    try {
      await getFreshAccessToken()
      if (account !== readCacheSession()) throw new Error('登录状态已改变，请在当前账号下重新操作')
      return requestWithAuth<T>(path, options, false)
    } catch (error) {
      // Only the refresh endpoint's explicit invalid-credential response is
      // grounds to discard credentials and the user's recovery journals.
      if (error instanceof ApiRequestError && error.statusCode === 401) {
        // Concurrent callers share the refresh promise and must redirect once.
        if (getRefreshToken() && getRefreshToken() === requestRefreshToken) {
          clearTokens()
          await Taro.reLaunch({ url: '/pages/login/index' })
        }
      }
      throw error
    }
  }

  if (response.statusCode < 200 || response.statusCode >= 300) {
    const payload = response.data as ApiErrorPayload
    throw new ApiRequestError(
      apiErrorPayloadMessage(payload, `请求失败（${response.statusCode}）`),
      response.statusCode,
      payload
    )
  }

  return response.data as T
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}, allowRefresh = true): Promise<T> {
  const write = options.authenticated !== false && Boolean(options.method && options.method !== 'GET')
  const account = readCacheSession()
  // Both successful and uncertain writes invalidate. No write is cached/replayed here.
  if (write) invalidateReadCache()
  try { return await requestWithAuth<T>(path, options, allowRefresh) }
  finally { if (write && account === readCacheSession()) invalidateReadCache() }
}

function apiErrorPayloadMessage (
  payload: ApiErrorPayload | null | undefined,
  fallback: string
): string {
  if (payload && typeof payload.message === 'string' && payload.message) {
    return payload.message
  }
  if (payload && typeof payload.detail === 'string' && payload.detail) {
    return payload.detail
  }
  if (payload?.detail && typeof payload.detail === 'object') {
    const nested = payload.detail as ApiErrorPayload
    if (typeof nested.message === 'string' && nested.message) return nested.message
    if (typeof nested.detail === 'string' && nested.detail) return nested.detail
  }
  return fallback
}

export function errorMessage(error: unknown, fallback: string): string {
  const friendly = (message: string): string => {
    if (/callContainer:fail.*timeout|code:\s*102002/i.test(message)) {
      return `${fallback}（网络连接超时）`
    }
    if (message.includes('[object Object]') || /callContainer:fail system error/i.test(message)) {
      return fallback
    }
    return message
  }

  if (error instanceof Error && error.message) return friendly(error.message)
  if (error && typeof error === 'object') {
    const candidate = error as { errMsg?: unknown; message?: unknown }
    if (typeof candidate.errMsg === 'string' && candidate.errMsg) return friendly(candidate.errMsg)
    if (typeof candidate.message === 'string' && candidate.message) return friendly(candidate.message)
  }
  return fallback
}
