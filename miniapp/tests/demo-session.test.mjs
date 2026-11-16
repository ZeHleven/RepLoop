import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'

function setup (request) {
  const store = new Map()
  const state = { clear: 0, saved: [], calls: [] }
  class ApiRequestError extends Error { constructor (statusCode) { super('request failed'); this.statusCode = statusCode } }
  const api = runtime('../../src/services/demo.ts', {
    '@tarojs/taro': { __esModule: true, default: {
      getStorageSync: key => store.get(key), setStorageSync: (key, value) => store.set(key, value), removeStorageSync: key => store.delete(key)
    } },
    '../core/storage': { clearTokens: () => { state.clear++ }, saveTokens: (...tokens) => state.saved.push(tokens) },
    '../core/request': { ApiRequestError, apiRequest: async (...args) => { state.calls.push(args); return request(...args) } }
  }, { __FITNESS_DEMO_MODE__: true }).exports
  return { api, state, ApiRequestError }
}
const credentials = { access_token: 'new-access', refresh_token: 'new-refresh', expires_at: '2027-01-01T00:00:00Z' }

test('entry retries reuse a cryptographic visitor key and only replace credentials after success', async () => {
  let attempts = 0
  const c = setup(async () => { if (attempts++ === 0) throw new Error('network'); return credentials })
  await assert.rejects(c.api.demoApi.start(), /network/)
  assert.equal(c.state.clear, 0)
  await c.api.demoApi.start()
  assert.equal(c.state.calls[0][1].data.request_key, c.state.calls[1][1].data.request_key)
  assert.match(c.state.calls[0][1].data.request_key, /^[0-9a-f]{64}$/)
  assert.deepEqual(c.state.saved, [['new-access', 'new-refresh']])
  assert.equal(c.state.clear, 1)
})

test('a confirmed expired visitor gets a new key, while 429 keeps the original key', async () => {
  let attempts = 0
  const c = setup(async () => { if (attempts++ === 0) throw new c.ApiRequestError(401); return credentials })
  await c.api.demoApi.start()
  assert.notEqual(c.state.calls[0][1].data.request_key, c.state.calls[1][1].data.request_key)
  const limited = setup(async () => { throw new limited.ApiRequestError(429) })
  await assert.rejects(limited.api.demoApi.start())
  assert.equal(limited.state.calls.length, 1)
  assert.equal(limited.state.clear, 0)
})

test('reset failure preserves the current session; successful reset clears old recovery caches', async () => {
  let fail = true
  const c = setup(async () => { if (fail) throw new Error('network'); return credentials })
  await assert.rejects(c.api.demoApi.reset())
  assert.equal(c.state.clear, 0)
  fail = false
  await c.api.demoApi.reset()
  assert.equal(c.state.calls[0][0], '/demo/reset')
  assert.equal(c.state.clear, 1)
})

test('H5 navigation appears after programmatic login and hides on a detail route without reloading', () => {
  const listeners = new Map()
  const browserWindow = { location: { hash: '#/pages/login/index' }, addEventListener () {}, removeEventListener () {} }
  const page = runtime('../../src/components/H5CapsuleHost.tsx', {
    '@tarojs/taro': { __esModule: true, default: { eventCenter: {
      on: (name, fn) => listeners.set(name, fn), off: name => listeners.delete(name)
    } } },
    '../custom-tab-bar/index': { __esModule: true, default: () => ({ type: 'View', props: { className: 'test-capsule' } }) }
  }, { window: browserWindow })
  page.render()
  assert.equal(page.find('test-capsule'), undefined)
  browserWindow.location.hash = '#/pages/agent/index'
  listeners.get('__afterTaroRouterChange')()
  page.render()
  assert.ok(page.find('test-capsule'))
  browserWindow.location.hash = '#/pages/history/index'
  listeners.get('__afterTaroRouterChange')()
  page.render()
  assert.equal(page.find('test-capsule'), undefined)
})
