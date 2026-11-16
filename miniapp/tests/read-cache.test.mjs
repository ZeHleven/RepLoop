import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime, deferred } from './helpers/page-runtime.mjs'

function setup () {
  let now = Date.parse('2026-09-11T03:00:00Z')
  class Clock extends Date { static now () { return now } }
  const cache = runtime('../../src/core/read-cache.ts', {}, { Date: Clock }).exports
  return { cache, advance: ms => { now += ms } }
}

test('reads coalesce, reuse a short snapshot, clone values, and force refresh explicitly', async () => {
  const { cache: c, advance } = setup(), response = deferred(); let calls = 0
  const read = () => { calls++; return response.promise }
  const first = c.readCached('plans', read), second = c.readCached('plans', read)
  response.resolve([{ name: '原始' }]); const values = await Promise.all([first, second])
  assert.equal(calls, 1); values[0][0].name = '编辑草稿'
  assert.equal(values[1][0].name, '原始'); assert.equal(c.peekCached('plans')[0].name, '原始')
  await c.readCached('plans', read); assert.equal(calls, 1)
  await c.readCached('plans', read, true); assert.equal(calls, 2)
  advance(30001); assert.equal(c.isCachedFresh('plans'), false)
  assert.ok(c.peekCached('plans'), 'same-day bounded stale snapshot can remain visible during refresh')
  await c.readCached('plans', read); assert.equal(calls, 3)
  advance(300001); assert.equal(c.peekCached('plans'), undefined)
})

test('failed refresh preserves last snapshot, is not cached as success and retries', async () => {
  const { cache: c } = setup()
  await c.readCached('today', async () => ({ calories: 800 }))
  await assert.rejects(c.readCached('today', async () => { throw Error('offline') }, true))
  assert.equal(c.peekCached('today').calories, 800)
  assert.equal(c.isCachedFresh('today'), false, 'a failed explicit refresh must not be hidden by a warm cache hit')
  assert.equal((await c.readCached('today', async () => ({ calories: 900 }))).calories, 900)
})

test('mutation invalidation rejects stale response and re-reads without storing old data', async () => {
  const { cache: c } = setup(), old = deferred(); let calls = 0
  const pending = c.readCached('plans', () => ++calls === 1 ? old.promise : Promise.resolve(['new']))
  c.invalidateReadCache(); old.resolve(['old'])
  assert.deepEqual(await pending, ['new']); assert.deepEqual(c.peekCached('plans'), ['new']); assert.equal(calls, 2)
})

test('logout/account change discards all snapshots and rejects in-flight old-account reads', async () => {
  const { cache: c } = setup(), old = deferred()
  await c.readCached('me', async () => ({ name: 'A' }))
  const pending = c.readCached('plans', () => old.promise)
  c.resetReadCacheSession(); assert.equal(c.peekCached('me'), undefined)
  await c.readCached('plans', async () => ['B'])
  old.resolve(['A']); await assert.rejects(pending, /登录状态已改变/)
  assert.deepEqual(c.peekCached('plans'), ['B'])
})

test('Beijing midnight invalidates today and week progress including reads crossing midnight', async () => {
  const { cache: c, advance } = setup(), old = deferred(); let calls = 0
  advance(13 * 3600000 - 100)
  await c.readCached('today', async () => 'yesterday')
  const pending = c.readCached('progress', () => ++calls === 1 ? old.promise : Promise.resolve('today'))
  advance(200); assert.equal(c.peekCached('today'), undefined)
  old.resolve('yesterday'); assert.equal(await pending, 'today')
})

test('entry count is bounded; cached null and empty results count as loaded', async () => {
  const { cache: c } = setup()
  await c.readCached('active', async () => null); assert.equal(c.peekCached('active'), null)
  await c.readCached('empty', async () => []); assert.deepEqual(c.peekCached('empty'), [])
  for (let i = 0; i < 50; i++) await c.readCached(`detail:${i}`, async () => i)
  assert.equal(c.peekCached('detail:0'), undefined); assert.equal(c.peekCached('detail:49'), 49)
})
