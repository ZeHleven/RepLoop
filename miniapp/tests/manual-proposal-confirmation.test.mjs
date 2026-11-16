import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'

const nativeFailure = { errMsg: 'showModal:fail The "fd" argument must be of type number. Received undefined' }
const snapshot = { name: '测试计划', duration_weeks: 4, days_per_week: 1, training_days: [1], exercises: [] }
const fixture = (type = 'plan_adjustment_v2') => ({
  id: 'proposal', version: 1, status: 'pending_confirmation', proposal_type: type,
  payload_fingerprint: 'original', allowed_actions: ['confirm', 'reject'],
  created_at: '2026-09-08T00:00:00Z', expires_at: '2099-01-01T00:00:00Z',
  payload: { proposal_type: type, before: snapshot, after: snapshot, changes: [],
    consequences: ['计划将永久删除，历史训练保留'], safety_notes: ['核对后确认'] }
})

async function createPage (options = {}) {
  let current = fixture(options.type)
  const hooks = {}, writes = [], modals = [], navigations = []
  const page = runtime('../../src/pages/plan-proposal-detail/index.tsx', {
    '../../core/request': { errorMessage: (e, fallback) => e?.errMsg || e?.message || fallback },
    '../../services/plan-management': { planManagementApi: {
      manualProposal: async () => options.read ? options.read(current) : structuredClone(current),
      pendingDecision: () => null,
      ...Object.fromEntries(['confirm', 'reject'].map(action => [action, async (id, version) => {
        writes.push({ action, id, version })
        if (options.decide) return options.decide(action)
        current = { ...current, status: action === 'confirm' ? 'applied' : 'rejected', version: 2, allowed_actions: [] }
        return { id, version: 2, status: current.status, payload_fingerprint: current.payload_fingerprint }
      }]))
    } },
    '@tarojs/taro': { __esModule: true,
      useLoad: fn => { hooks.load = fn }, useDidShow: fn => { hooks.show = fn }, useDidHide: fn => { hooks.hide = fn },
      default: {
        showModal: data => { modals.push(data); return options.modal ? options.modal(data) : Promise.reject(nativeFailure) },
        showToast: () => { throw nativeFailure },
        switchTab: async data => { navigations.push(data); if (options.navigationFails) throw new Error('navigation failed') },
        navigateBack: async () => { navigations.push('back'); if (options.navigationFails) throw new Error('navigation failed') }
      }
    }
  })
  page.render(); hooks.load({ id: 'proposal' }); await page.flush()
  return { ...page, hooks, writes, modals, navigations, change: patch => { current = { ...current, ...patch } } }
}
async function review (page) { page.click('review-row'); await page.flush() }

test('opening the decision always uses inline confirmation without invoking native modal APIs', async () => {
  const page = await createPage()
  await review(page); await page.click('primary-button'); await page.flush()
  assert.equal(page.modals.length, 0)
  assert.equal(page.writes.length, 0)
  assert.ok(page.find('inline-confirmation-panel'))
})

for (const kind of ['rejection', 'throw', 'invalid result']) {
  test(`manual proposal does not invoke native ${kind}; inline cancellation writes nothing`, async () => {
    const page = await createPage({ modal: () => {
      if (kind === 'throw') throw nativeFailure
      if (kind === 'invalid result') return Promise.resolve({})
      return Promise.reject(nativeFailure)
    } })
    await review(page)
    await page.click('primary-button'); await page.flush()
    assert.equal(page.writes.length, 0)
    assert.equal(page.modals.length, 0)
    assert.ok(page.find('inline-confirmation-panel'))
    assert.match(page.text(), /当前计划尚未修改/)
    page.click('cancel-inline-confirmation'); await page.flush()
    assert.equal(page.find('inline-confirmation-panel'), undefined)
    assert.equal(page.writes.length, 0)
  })
}

for (const action of ['confirm', 'reject']) {
  test(`inline ${action} writes once and preserves authoritative result despite native UI failure`, async () => {
    const page = await createPage({ navigationFails: true })
    await review(page)
    await page.click(action === 'confirm' ? 'primary-button' : 'secondary-button'); await page.flush()
    assert.equal(page.writes.length, 0)
    const submit = page.find('accept-inline-confirmation').props.onClick
    await Promise.all([submit(), submit()]); await page.flush()
    assert.deepEqual(page.writes, [{ id: 'proposal', version: 1, action }])
    assert.match(page.text(), action === 'confirm' ? /已应用/ : /已拒绝/)
    assert.equal(page.find('error-banner'), undefined)
    assert.equal(page.find('review-row'), undefined)
    assert.equal(page.modals.length, 0, 'no native modal before or after an authoritative decision')
    await page.click('return-to-training'); await page.flush()
    assert.equal(page.writes.length, 1)
  })
}

test('permanent deletion preserves its warning and requires separate inline acceptance', async () => {
  const page = await createPage({ type: 'plan_deletion_v1' })
  await review(page); await page.click('danger-button'); await page.flush()
  assert.equal(page.writes.length, 0)
  assert.match(JSON.stringify(page.find('inline-confirmation-panel')), /永久删除/)
  assert.match(JSON.stringify(page.find('inline-confirmation-panel')), /历史训练/)
  await page.click('accept-inline-confirmation'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.match(page.text(), /计划已永久删除/)
})

test('inline cancel invalidates captured acceptance and never writes', async () => {
  const page = await createPage()
  await review(page); await page.click('primary-button'); await page.flush()
  const oldAccept = page.find('accept-inline-confirmation').props.onClick
  page.click('cancel-inline-confirmation'); await page.flush()
  await oldAccept(); await page.flush()
  assert.equal(page.writes.length, 0)
  assert.equal(page.find('inline-confirmation-panel'), undefined)
})

test('overlapping confirmation clicks are locked and late acceptance after leaving cannot write', async () => {
  const page = await createPage()
  await review(page)
  page.click('primary-button'); page.click('primary-button')
  await page.flush()
  assert.equal(page.modals.length, 0)
  assert.equal(page.findAll('inline-confirmation-panel').length, 1)
  const oldAccept = page.find('accept-inline-confirmation').props.onClick
  page.hooks.hide()
  await oldAccept(); await page.flush()
  assert.equal(page.writes.length, 0)
})

test('refresh invalidates inline permission when proposal version or status changes', async () => {
  const page = await createPage()
  await review(page); await page.click('primary-button'); await page.flush()
  const staleClick = page.find('accept-inline-confirmation').props.onClick
  page.change({ version: 2, status: 'stale', allowed_actions: [] })
  await page.click('refresh-button'); await page.flush()
  await staleClick(); await page.flush()
  assert.equal(page.writes.length, 0)
  assert.equal(page.find('inline-confirmation-panel'), undefined)
  assert.match(page.text(), /已失效/)
})

test('expired or unreviewed proposal cannot request a decision', async () => {
  const page = await createPage()
  await page.click('primary-button'); await page.flush()
  assert.equal(page.modals.length, 0)
  page.change({ expires_at: '2000-01-01T00:00:00Z' })
  await page.click('refresh-button'); await page.flush()
  await review(page); await page.click('primary-button'); await page.flush()
  assert.equal(page.modals.length, 0)
  assert.equal(page.writes.length, 0)
})

test('even with a working native API, only a separate inline acceptance submits', async () => {
  const page = await createPage({ modal: async () => ({ confirm: true, cancel: false }) })
  await review(page)
  const submit = page.find('primary-button').props.onClick
  await submit(); await page.flush(); await submit(); await page.flush()
  assert.equal(page.writes.length, 0)
  await page.click('accept-inline-confirmation'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.equal(page.modals.length, 0)
  assert.equal(page.navigations.length, 1)
  assert.match(page.text(), /新活动计划已经生效/)
})

test('lost decision response is reconciled as applied without resubmission', async () => {
  let committed = false
  const page = await createPage({
    read: async current => committed ? { ...current, status: 'applied', version: 2, allowed_actions: [] } : current,
    decide: async () => { committed = true; throw new Error('response lost') }
  })
  await review(page); await page.click('primary-button'); await page.flush()
  await page.click('accept-inline-confirmation'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.equal(page.find('error-banner'), undefined)
  assert.equal(page.find('review-row'), undefined)
  assert.match(page.text(), /已从服务端核实/)
  assert.match(page.text(), /已应用/)
})

test('failed verification blocks repeat decisions until manual refresh and a new explicit confirmation', async () => {
  let offline = false
  const page = await createPage({
    read: async current => { if (offline) throw new Error('offline'); return current },
    decide: async () => { offline = true; throw new Error('connection lost') }
  })
  await review(page); await page.click('primary-button'); await page.flush()
  await page.click('accept-inline-confirmation'); await page.flush()
  await review(page); await page.click('primary-button'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.equal(page.find('primary-button').props.disabled, true)
  assert.match(page.text(), /请先刷新服务端状态/)
  offline = false
  await page.click('refresh-button'); await page.flush()
  assert.equal(page.find('primary-button').props.disabled, true, 'refresh does not imply review or consent')
  await review(page); await page.click('primary-button'); await page.flush()
  assert.equal(page.writes.length, 1, 'second prompt alone does not retry')
  await page.click('accept-inline-confirmation'); await page.flush()
  assert.equal(page.writes.length, 2)
})

test('refreshing a pending newer version clears previous inline consent and requires reviewing again', async () => {
  const page = await createPage()
  await review(page); await page.click('primary-button'); await page.flush()
  const oldAccept = page.find('accept-inline-confirmation').props.onClick
  page.change({ version: 2, payload_fingerprint: 'new content' })
  await page.click('refresh-button'); await page.flush()
  await oldAccept(); await page.flush()
  assert.equal(page.writes.length, 0)
  assert.equal(page.find('primary-button').props.disabled, true)
  await review(page); await page.click('primary-button'); await page.flush()
  await page.click('accept-inline-confirmation'); await page.flush()
  assert.equal(page.writes[0].version, 2)
})
