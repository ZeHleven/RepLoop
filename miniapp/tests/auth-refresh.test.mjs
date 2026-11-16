import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred, runtime } from './helpers/page-runtime.mjs'

function client (refresh, businessStatus = 200) {
  const state = { access: 'expired', refresh: 'refresh', cleared: 0, redirects: 0, calls: [] }
  const api = runtime('../../src/core/request.ts', {
    './storage': {
      getAccessToken: () => state.access, getRefreshToken: () => state.refresh,
      saveTokens: (a, r) => { state.access = a; state.refresh = r },
      clearTokens: () => { state.cleared++; state.access = ''; state.refresh = '' }
    },
    '@tarojs/taro': { __esModule: true, default: {
      request: async options => {
        state.calls.push(options)
        if (options.url.endsWith('/auth/refresh')) return refresh()
        return { statusCode: state.access === 'expired' ? 401 : businessStatus, data: { detail: '业务响应' } }
      },
      reLaunch: async () => { state.redirects++ }
    } }
  }, { __FITNESS_API_BASE_URL__: 'https://test.invalid/api/v1', __FITNESS_CLOUD_ENV__: '', __FITNESS_CLOUD_SERVICE__: '' }).exports
  return { state, api }
}
for (const status of [401, 403, 429, 500, 503]) {
  test(`refresh ${status} only clears state for explicit 401`, async () => {
    const c = client(async () => ({ statusCode: status, data: { detail: '失败' } }))
    await assert.rejects(c.api.apiRequest('/profile'))
    assert.equal(c.state.cleared, status === 401 ? 1 : 0)
    assert.equal(c.state.redirects, status === 401 ? 1 : 0)
    assert.equal(c.state.calls.length, 2)
  })
}
test('network interruption preserves state and never retries mutation automatically', async () => {
  const c = client(async () => { throw new Error('timeout') })
  await assert.rejects(c.api.apiRequest('/meals', { method: 'POST', data: {} }))
  assert.equal(c.state.cleared, 0)
  assert.equal(c.state.calls.length, 2)
})
for (const status of [200, 401]) {
  test(`concurrent refresh ${status} is single-flight and redirects at most once`, async () => {
    const response = deferred()
    const c = client(() => response.promise)
    const requests = Promise.allSettled([c.api.apiRequest('/profile'), c.api.apiRequest('/meals/today')])
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(c.state.calls.filter(x => x.url.endsWith('/auth/refresh')).length, 1)
    response.resolve({ statusCode: status, data: { access_token: 'fresh', refresh_token: 'fresh-refresh' } })
    const results = await requests
    assert.ok(results.every(r => r.status === (status === 200 ? 'fulfilled' : 'rejected')))
    assert.equal(c.state.redirects, status === 401 ? 1 : 0)
  })
}
test('a post-refresh business error is not an invalid login', async () => {
  const c = client(async () => ({ statusCode: 200, data: { access_token: 'fresh', refresh_token: 'r' } }), 500)
  await assert.rejects(c.api.apiRequest('/profile'))
  assert.equal(c.state.cleared, 0)
  assert.equal(c.state.calls.length, 3)
})
test('malformed success response does not corrupt saved tokens', async () => {
  const c = client(async () => ({ statusCode: 200, data: {} }))
  await assert.rejects(c.api.apiRequest('/profile'))
  assert.equal(c.state.access, 'expired'); assert.equal(c.state.refresh, 'refresh')
  assert.equal(c.state.cleared, 0)
})
for (const status of [200, 401]) {
  test(`late refresh ${status} cannot overwrite or clear a newer login`, async () => {
    const response = deferred()
    const c = client(() => response.promise)
    const request = assert.rejects(c.api.apiRequest('/meals', { method: 'POST', data: {} }))
    await new Promise(resolve => setImmediate(resolve))
    c.state.access = 'new-account'; c.state.refresh = 'new-account-refresh'
    response.resolve({ statusCode: status, data: { access_token: 'old-account', refresh_token: 'old-refresh' } })
    await request
    assert.equal(c.state.cleared, 0); assert.equal(c.state.access, 'new-account'); assert.equal(c.state.calls.length, 2)
  })
}
