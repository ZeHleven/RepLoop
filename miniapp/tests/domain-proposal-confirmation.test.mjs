import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime, deferred } from './helpers/page-runtime.mjs'

const failure = { errMsg: 'showModal:fail The "fd" argument must be of type number. Received undefined' }
const fixture = (type = 'meal_log_create_v1') => ({
  id: 'domain-proposal', version: 1, status: 'pending_confirmation', proposal_type: type,
  payload_fingerprint: 'original', allowed_actions: ['confirm', 'reject'],
  expires_at: '2099-01-01T00:00:00Z',
  payload: { before: null, after: { logged_at: '2026-09-10', meal_type: 'breakfast', items: [{
    id: 'internal-item', food_id: 'standard-food', custom_food_id: 'private-source', custom_food_version: 7,
    food_name: '燕麦', amount_g: 120, calories: 466.8, protein_g: 20.4, carbs_g: 79.2, fat_g: 8.4
  }] }, safety_notes: ['确认后才写入饮食记录'] }
})

async function createPage (options = {}) {
  let current = fixture(options.type)
  const hooks = {}, calls = { reads: 0, writes: [], native: 0 }
  const page = runtime('../../src/pages/domain-proposal-detail/index.tsx', {
    '../../core/request': { errorMessage: (e, f) => e?.errMsg || e?.message || f },
    '../../services/plan-management': { planManagementApi: {
      proposal: async id => { calls.reads++; return options.read ? options.read(structuredClone(current), id) : structuredClone(current) },
      pendingDecision: () => null,
      ...Object.fromEntries(['confirm', 'reject'].map(action => [action, async (id, version) => {
        calls.writes.push({ action, id, version })
        if (options.decide) return options.decide(action)
        current = { ...current, version: 2, status: action === 'confirm' ? 'applied' : 'rejected', allowed_actions: [] }
        return { id, version: 2, status: current.status, payload_fingerprint: current.payload_fingerprint, result_data: {} }
      }]))
    } },
    '@tarojs/taro': { __esModule: true,
      useLoad: fn => { hooks.load = fn }, useDidShow: fn => { hooks.show = fn },
      useDidHide: fn => { hooks.hide = fn }, useUnload: fn => { hooks.unload = fn },
      default: {
        showModal: async () => { calls.native++; throw failure },
        showToast: () => { calls.native++; throw failure }
      }
    }
  })
  page.render(); hooks.load({ id: 'domain-proposal' }); await page.flush()
  return { ...page, hooks, calls, change: patch => { current = { ...current, ...patch } } }
}
async function open (page, action = 'confirm') {
  page.click('review-row'); await page.flush()
  await page.click(action === 'confirm' ? 'primary-button' : 'secondary-button'); await page.flush()
}

test('meal proposal hides internal source identifiers without hiding food/nutrients', async () => {
  const page = await createPage()
  for (const value of ['custom_food_id', 'custom_food_version', 'private-source', 'internal-item', 'standard-food']) assert.ok(!page.text().includes(value))
  for (const value of ['燕麦', '120', '466.8', '20.4', '79.2', '8.4']) assert.ok(page.text().includes(value))
  assert.deepEqual(page.calls.writes, [])
})

for (const type of ['meal_log_create_v1', 'meal_log_delete_v1']) {
  test(`${type} food item rows follow reading order regardless of payload key order`, async () => {
    const items = [
      { fat_g: 8.4, carbs_g: 79.2, amount_g: 120, calories: 466.8, food_name: '燕麦', protein_g: 20.4, custom_food_id: null, custom_food_version: null },
      { protein_g: 0, calories: 0, food_name: '零热量食品', fat_g: 0, amount_g: 150, carbs_g: 0, food_id: 'hidden' }
    ]
    const original = fixture(type)
    original.payload = type === 'meal_log_delete_v1' ? { before: { items }, after: null } : { before: null, after: { items } }
    const snapshot = structuredClone(original)
    const page = await createPage({ type, read: () => original })
    assert.deepEqual(page.findAll('value-label').map(node => node.props.children), [
      '食品', '克数', '热量', '碳水', '蛋白质', '脂肪',
      '食品', '克数', '热量', '碳水', '蛋白质', '脂肪'
    ])
    assert.deepEqual(page.findAll('value-text').map(node => node.props.children), [
      '燕麦', '120 g', '466.8 kcal', '79.2 g', '20.4 g', '8.4 g', '零热量食品', '150 g', '0 kcal', '0 g', '0 g', '0 g'
    ])
    assert.deepEqual(original, snapshot, 'display ordering must not change the proposal payload')
    assert.deepEqual(page.calls.writes, [])
  })
}

test('food item ordering does not fabricate missing fields or discard other visible information', async () => {
  const original = fixture()
  original.payload = { after: { items: [{ name: '保留的说明', calories: 0, food_name: '测试食品', custom_food_id: 'hidden' }] } }
  const page = await createPage({ read: () => original })
  assert.deepEqual(page.findAll('value-label').map(node => node.props.children), ['食品', '热量', '名称'])
  assert.deepEqual(page.findAll('value-text').map(node => node.props.children), ['测试食品', '0 kcal', '保留的说明'])
  assert.deepEqual(page.calls.writes, [])
})

test('food units preserve decimals, numeric strings and missing values without changing payload', async () => {
  const original = fixture()
  original.payload = { after: { items: [{ food_name: '测试食品', amount_g: 120.5, calories: '466.8', carbs_g: null, protein_g: '0', fat_g: '' }] } }
  const snapshot = structuredClone(original)
  const page = await createPage({ read: () => original })
  assert.deepEqual(page.findAll('value-text').map(node => node.props.children), ['测试食品', '120.5 g', '466.8 kcal', '未设置', '0 g', '未设置'])
  assert.deepEqual(original, snapshot)
  assert.deepEqual(page.calls.writes, [])
})

test('food units are not appended to non-numeric descriptions or unrelated proposal fields', async () => {
  const original = fixture()
  original.payload = { after: { items: [{ food_name: '测试食品', amount_g: ' ', calories: '未知', carbs_g: false, protein_g: '20 g', fat_g: undefined }] } }
  const page = await createPage({ read: () => original })
  assert.deepEqual(page.findAll('value-text').map(node => node.props.children), ['测试食品', ' ', '未知', '否', '20 g', '未设置'])
  const weight = fixture('weight_log_create_v1')
  weight.payload = { after: { weight_kg: 80, recorded_at: '2026-09-10' } }
  const weightPage = await createPage({ type: 'weight_log_create_v1', read: () => weight })
  assert.deepEqual(weightPage.findAll('value-text').map(node => node.props.children), ['80', '2026-09-10'])
  assert.deepEqual(page.calls.writes, [])
  assert.deepEqual(weightPage.calls.writes, [])
})

for (const type of ['meal_log_create_v1', 'meal_log_delete_v1', 'daily_meal_log_create_v1', 'weight_log_create_v1', 'profile_update_v1', 'plan_creation_v1']) {
  for (const action of ['confirm', 'reject']) {
    test(`${type} ${action} uses explicit inline consent and commits once without native UI`, async () => {
      const page = await createPage({ type })
      await open(page, action)
      assert.ok(page.find('inline-confirmation-panel'))
      assert.equal(page.calls.writes.length, 0)
      const accept = page.find('accept-inline-confirmation').props.onClick
      await Promise.all([accept(), accept()]); await page.flush()
      assert.deepEqual(page.calls.writes, [{ action, id: 'domain-proposal', version: 1 }])
      assert.equal(page.calls.native, 0)
      assert.equal(page.find('error-banner'), undefined)
      assert.equal(page.find('review-row'), undefined)
      assert.match(page.text(), action === 'confirm' ? /已应用/ : /已拒绝/)
    })
  }
}

test('cancelled, superseded, hidden and unloaded consent cannot be reused', async () => {
  for (const event of ['cancel', 'hide', 'unload']) {
    const page = await createPage(); await open(page)
    const stale = page.find('accept-inline-confirmation').props.onClick
    if (event === 'cancel') page.click('cancel-inline-confirmation')
    else page.hooks[event]()
    await page.flush(); await stale(); await page.flush()
    assert.equal(page.calls.writes.length, 0)
    if (event === 'cancel') {
      await open(page, 'reject')
      await stale(); await page.flush()
      assert.equal(page.calls.writes.length, 0, 'old confirm cannot accept the new reject prompt')
    }
  }
})

test('unreviewed, terminal, expired or disallowed proposals never submit', async () => {
  const page = await createPage()
  await page.click('primary-button'); await page.flush()
  assert.equal(page.calls.writes.length, 0)
  assert.equal(page.find('inline-confirmation-panel'), undefined)
  for (const patch of [{ expires_at: '2000-01-01T00:00:00Z' }, { expires_at: 'invalid' }, { expires_at: '2099-01-01T00:00:00Z', allowed_actions: [] }]) {
    page.change(patch); await page.click('refresh-button'); await page.flush(); await open(page)
    assert.equal(page.find('inline-confirmation-panel'), undefined)
  }
  for (const status of ['applied', 'rejected', 'expired', 'stale', 'failed']) {
    page.change({ status }); await page.click('refresh-button'); await page.flush()
    assert.equal(page.find('review-row'), undefined)
  }
  assert.equal(page.calls.writes.length, 0)
})

test('refresh invalidates review for version or fingerprint changes', async () => {
  for (const patch of [{ version: 2 }, { payload_fingerprint: 'changed' }]) {
    const page = await createPage(); await open(page)
    const stale = page.find('accept-inline-confirmation').props.onClick
    page.change(patch); await page.click('refresh-button'); await page.flush(); await stale(); await page.flush()
    assert.equal(page.calls.writes.length, 0)
    assert.equal(page.find('inline-confirmation-panel'), undefined)
  }
})

test('lost response reconciles applied result, never resubmits or reports toast failure', async () => {
  let committed = false
  const page = await createPage({
    decide: async () => { committed = true; throw new Error('response lost') },
    read: async current => committed ? { ...current, status: 'applied', version: 2, allowed_actions: [] } : current
  })
  await open(page); await page.click('accept-inline-confirmation'); await page.flush()
  assert.equal(page.calls.writes.length, 1)
  assert.match(page.text(), /已应用/)
  assert.equal(page.find('error-banner'), undefined)
  assert.equal(page.calls.native, 0)
})

test('uncertain write and failed read preserve content/error and disable retry until refreshed', async () => {
  let attempted = false, unavailable = true
  const page = await createPage({
    decide: async () => { attempted = true; throw new Error('network unavailable') },
    read: async current => { if (attempted && unavailable) throw new Error('read unavailable'); return current }
  })
  await open(page); await page.click('accept-inline-confirmation'); await page.flush()
  assert.match(page.text(), /燕麦/)
  assert.ok(page.find('error-banner'))
  await page.click('primary-button'); await page.flush()
  assert.equal(page.calls.writes.length, 1)
  unavailable = false; await page.click('refresh-button'); await page.flush()
  assert.equal(page.calls.writes.length, 1)
  await page.click('primary-button'); await page.flush()
  assert.equal(page.find('inline-confirmation-panel'), undefined, 'fresh review required')
})

test('version conflict refreshes authoritative status and preserves an explanatory error', async () => {
  let attempted = false
  const page = await createPage({
    decide: async () => { attempted = true; throw new Error('version conflict') },
    read: async current => attempted ? { ...current, version: 2, status: 'stale', allowed_actions: [] } : current
  })
  await open(page); await page.click('accept-inline-confirmation'); await page.flush()
  assert.match(page.text(), /已失效/)
  assert.match(page.text(), /version conflict/)
  assert.equal(page.find('review-row'), undefined)
  assert.equal(page.calls.writes.length, 1)
})

test('late read cannot overwrite a newer snapshot or an authoritative decision', async () => {
  const older = deferred(); let reads = 0
  const page = await createPage({ read: current => ++reads === 2 ? older.promise : Promise.resolve(current) })
  const first = page.click('refresh-button')
  await page.flush()
  page.change({ version: 3, payload_fingerprint: 'fresh' })
  await page.click('refresh-button'); await page.flush()
  await open(page); await page.click('accept-inline-confirmation'); await page.flush()
  older.resolve(fixture()); await first; await page.flush()
  assert.equal(page.calls.writes[0].version, 3)
  assert.match(page.text(), /已应用/)
  assert.equal(page.find('review-row'), undefined)
})

test('failed initial load offers read-only retry; returning after hidden load refetches', async () => {
  let unavailable = true
  const page = await createPage({ read: async current => { if (unavailable) throw new Error('offline'); return current } })
  assert.ok(page.find('error-banner'))
  assert.ok(page.find('refresh-button'))
  unavailable = false; await page.click('refresh-button'); await page.flush()
  assert.ok(page.find('review-row'))
  page.hooks.hide(); await page.flush(); page.hooks.show(); await page.flush()
  assert.ok(page.find('review-row'))
  assert.equal(page.calls.writes.length, 0)
})

test('first load and didShow share an in-flight read and cannot write before review', async () => {
  const read = deferred()
  const page = await createPage({ read: () => read.promise })
  page.hooks.show(); await page.flush()
  assert.equal(page.calls.reads, 1)
  read.resolve(fixture()); await page.flush()
  await page.click('primary-button'); await page.flush()
  assert.equal(page.find('inline-confirmation-panel'), undefined)
  assert.equal(page.calls.writes.length, 0)
})

test('hide and return before or after a decision response reconciles without replaying it', async () => {
  for (const returnBeforeResponse of [true, false]) {
    const response = deferred(); let committed = false
    const page = await createPage({
      decide: async () => { const value = await response.promise; committed = true; return value },
      read: async current => committed ? { ...current, status: 'applied', version: 2, allowed_actions: [] } : current
    })
    await open(page)
    const submission = page.click('accept-inline-confirmation'); await page.flush()
    page.hooks.hide(); await page.flush()
    if (returnBeforeResponse) { page.hooks.show(); await page.flush() }
    response.resolve({ id: 'domain-proposal', version: 2, status: 'applied', payload_fingerprint: 'original', result_data: {} })
    await submission; await page.flush()
    if (!returnBeforeResponse) { page.hooks.show(); await page.flush() }
    assert.match(page.text(), /已应用/)
    assert.equal(page.find('review-row'), undefined)
    assert.equal(page.calls.writes.length, 1)
  }
})

test('failed decision returning from background stays unverified after failed reconciliation', async () => {
  const response = deferred(); let failed = false
  const page = await createPage({
    decide: async () => { try { return await response.promise } finally { failed = true } },
    read: async current => { if (failed) throw new Error('offline'); return current }
  })
  await open(page)
  const submission = page.click('accept-inline-confirmation'); await page.flush()
  page.hooks.hide(); page.hooks.show(); await page.flush()
  response.reject(new Error('response lost')); await submission; await page.flush()
  assert.ok(page.find('error-banner'))
  await page.click('primary-button'); await page.flush()
  assert.equal(page.find('inline-confirmation-panel'), undefined)
  assert.equal(page.calls.writes.length, 1)
})

test('late response from a different proposal cannot replace the current detail', async () => {
  const response = deferred()
  const page = await createPage({ decide: () => response.promise })
  await open(page)
  const submission = page.click('accept-inline-confirmation'); await page.flush()
  page.hooks.hide(); await page.flush()
  page.change({ id: 'another-proposal', payload_fingerprint: 'another' })
  page.hooks.load({ id: 'another-proposal' }); page.hooks.show(); await page.flush()
  response.resolve({ id: 'domain-proposal', status: 'applied', version: 2, payload_fingerprint: 'original' })
  await submission; await page.flush(); await page.flush()
  assert.match(page.text(), /待你确认/)
  assert.doesNotMatch(page.text(), /变更已经成功应用/)
  assert.equal(page.calls.writes.length, 1)
})

test('deletion warning and second confirmation survive, cancel never deletes', async () => {
  const page = await createPage({ type: 'meal_log_delete_v1' })
  page.change({ payload: { before: fixture().payload.after, after: null } })
  await page.click('refresh-button'); await page.flush(); await open(page)
  assert.match(JSON.stringify(page.find('inline-confirmation-panel')), /删除上方记录/)
  page.click('cancel-inline-confirmation'); await page.flush()
  assert.equal(page.calls.writes.length, 0)
  assert.match(page.text(), /燕麦/)
})

test('null private food metadata stays hidden while explicit zero nutrients remain visible', async () => {
  const page = await createPage()
  page.change({ payload: { after: { items: [{ food_name: '零热量测试', amount_g: 100, calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0, custom_food_id: null, custom_food_version: null }] } } })
  await page.click('refresh-button'); await page.flush()
  assert.doesNotMatch(page.text(), /custom_food_|未设置/)
  assert.match(page.text(), /零热量测试/)
  assert.equal(page.findAll('value-text').filter(node => ['0 kcal', '0 g'].includes(node.props.children)).length, 4)
})
