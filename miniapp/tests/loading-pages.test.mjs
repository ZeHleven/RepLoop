import assert from 'node:assert/strict'
import test from 'node:test'
import { loadingPage } from './helpers/loading-fixtures.mjs'
import { deferred } from './helpers/page-runtime.mjs'
import { manualClock } from './helpers/interaction-runtime.mjs'

for (const [name, count, content] of [['workouts',4,'plan-card'],['me',2,'profile-summary'],['history',2,'history-card'],['nutrition',2,'daily-card'],['workout-detail',1,'detail-heading']]) {
  test(`${name}: cold loads ${count} reads; warm return reads zero and retains content; invalidation re-reads`, async () => {
    const page = loadingPage(name)
    await page.show(); assert.equal(page.calls.length,count); assert.ok(page.find(content))
    await page.hide(); await page.show(); assert.equal(page.calls.length,count); assert.ok(page.find(content))
    await page.request('/profile',{method:'PUT',data:{weight_kg:81}})
    await page.hide(); await page.show(); assert.equal(page.calls.filter(c => c.method === 'GET').length,count * 2)
    assert.ok(page.find(content)); page.unmount()
  })
}

test('workouts launch independent GETs together and discard a hidden page result', async () => {
  const wait = deferred(), page = loadingPage('workouts',{transport:() => wait.promise})
  await page.show(); assert.equal(page.calls.length,4); assert.ok(page.find('read-wave'))
  await page.hide(); assert.equal(page.find('read-wave'),undefined)
  wait.resolve(); await page.flush(); assert.equal(page.find('plan-card'),undefined)
  await page.show(); assert.ok(page.find('plan-card')); assert.equal(page.calls.length,4)
  page.unmount()
})

test('nutrition cache expiry/failure/retry keep 150g draft and avoid food search reload', async () => {
  const clock = manualClock(Date.parse('2026-09-11T03:00:00Z')); let fail = false
  const page = loadingPage('nutrition',{globals:clock.globals,transport:path => { if (fail && path === '/meals/today') throw Error('离线') }})
  await page.show(); await page.click('edit-meal'); await page.flush()
  page.input('selected-amount-input','150'); await page.flush()
  const key = page.find('selected-row').key
  clock.advance(30001); fail = true; await page.hide(); await page.show()
  assert.ok(page.find('daily-card')); assert.ok(page.find('today-retry'))
  assert.equal(page.find('selected-amount-input').props.value,'150'); assert.equal(page.find('selected-row').key,key)
  fail = false; await page.click('today-retry'); await page.flush()
  assert.equal(page.find('today-retry'),undefined); assert.equal(page.find('selected-amount-input').props.value,'150')
  assert.equal(page.calls.filter(c => c.path === '/foods/library').length,1)
  assert.ok(page.calls.every(c => c.method === 'GET')); page.unmount()
})

test('Beijing next-day return clears yesterday energy while pending without changing draft date', async () => {
  const clock = manualClock(Date.parse('2026-09-11T15:59:59Z')), wait = deferred(); let slow = false
  const page = loadingPage('nutrition',{globals:clock.globals,transport:() => slow ? wait.promise : undefined})
  await page.show(); await page.click('edit-meal'); await page.flush(); page.input('selected-amount-input','150'); await page.flush()
  await page.hide(); clock.advance(2000); slow = true; await page.show()
  assert.equal(page.find('daily-card'),undefined); assert.equal(page.find('selected-amount-input').props.value,'150')
  assert.match(page.text(),/2026-09-11/)
  page.data.today.date = '2026-09-12'; wait.resolve(); await page.flush(); assert.ok(page.find('daily-card')); page.unmount()
})

test('logout/new login cannot populate cache or UI from the old pending account', async () => {
  const wait = deferred(); const page = loadingPage('me',{transport:() => wait.promise})
  await page.show(); const storage = page.loadSource('../../src/core/storage.ts')
  storage.clearTokens(); storage.saveTokens('new-account','new-refresh'); wait.resolve(); await page.flush()
  assert.equal(page.cache.peekCached('profile'),undefined); assert.equal(page.find('profile-summary'),undefined)
  assert.match(page.text(),/登录状态已改变/); page.unmount()
})

for (const method of ['POST','PUT','DELETE']) {
  test(`${method} uncertainty invalidates reads without repeating the mutation`, async () => {
    const page = loadingPage('me',{transport:(_,options) => { if (options.method === method) throw Error('timeout') }})
    await page.show(); assert.ok(page.cache.peekCached('profile'))
    await assert.rejects(page.request('/profile',{method,data:{}}))
    assert.equal(page.cache.peekCached('profile'),undefined)
    assert.equal(page.calls.filter(c => c.method === method).length,1); page.unmount()
  })
}

test('Agent terminal response invalidates overview cache; normal API GETs remain uncached', async () => {
  const page = loadingPage('me'); await page.show()
  const agent = page.loadSource('../../src/services/agent.ts').agentApi
  await agent.run('run'); assert.equal(page.cache.peekCached('profile'),undefined)
  await page.request('/profile'); await page.request('/profile')
  assert.equal(page.calls.filter(c => c.path === '/profile').length,3); page.unmount()
})

test('successful token renewal preserves session-local cache identity', async () => {
  const page = loadingPage('me'); await page.show()
  const storage = page.loadSource('../../src/core/storage.ts'), before = page.cache.readCacheSession()
  storage.saveTokens('renewed','renewed-refresh',true)
  assert.equal(page.cache.readCacheSession(),before); assert.ok(page.cache.peekCached('profile'))
  storage.saveTokens('login','login-refresh')
  assert.notEqual(page.cache.readCacheSession(),before); assert.equal(page.cache.peekCached('profile'),undefined); page.unmount()
})

test('Me does not display an unknown weight history as zero from a profile-only cache', async () => {
  const page = loadingPage('me',{deferRender:true})
  await page.cache.readCached('profile',async () => page.data.profile)
  page.render(); assert.equal(page.find('profile-summary'),undefined)
  await page.show(); assert.ok(page.find('profile-summary'))
  assert.equal(page.calls.length,1); assert.equal(page.calls[0].path,'/profile/weight')
  page.unmount()
})

test('history restores only ended sessions, never unfinished cached sessions', async () => {
  const page = loadingPage('history',{deferRender:true})
  await page.cache.readCached('workout-history',async () => [page.data.session,{...page.data.session,id:'pending',status:'in_progress',plan_name:'未结束场次'}])
  await page.cache.readCached('progress:8',async () => page.data.progress)
  page.render(); assert.ok(page.find('history-card')); assert.doesNotMatch(page.text(),/未结束场次/)
  await page.show(); assert.equal(page.calls.length,0); page.unmount()
})

test('LoadingFeedback ends when reads settle and has no detached animation after hide/unmount', async () => {
  const wait = deferred(), page = loadingPage('history',{transport:()=>wait.promise})
  await page.show(); assert.ok(page.find('read-wave'))
  await page.hide(); assert.equal(page.find('read-wave'),undefined)
  wait.resolve(); await page.flush(); await page.show()
  assert.equal(page.find('read-wave'),undefined); assert.ok(page.find('history-card')); page.unmount()
})
