import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred, runtime } from './helpers/page-runtime.mjs'

async function createPage (overrides = {}) {
  const writes = [], navigation = [], toast = [], hooks = {}
  const profile = { age: 30, height_cm: 180, gender: 'male', primary_goal: 'strength',
    experience_level: 'beginner', training_location: 'gym', training_days_per_week: 1, session_duration_min: 30 }
  const page = runtime('../../src/pages/profile-edit/index.tsx', {
    '../../services/profile': { profileApi: {
      get: async () => profile,
      update: async data => { writes.push(data); return overrides.update ? overrides.update(data) : data }
    } },
    '../../core/request': { errorMessage: (error, fallback) => error?.errMsg || error?.message || fallback },
    '@tarojs/taro': { __esModule: true, useLoad: fn => { hooks.load = fn }, default: {
      showToast: (...args) => { toast.push(args); return overrides.showToast ? overrides.showToast(...args) : Promise.resolve() },
      navigateBack: (...args) => { navigation.push(args); return overrides.navigateBack ? overrides.navigateBack(...args) : Promise.resolve() }
    } }
  })
  page.render(); hooks.load(); await page.flush()
  const gender = page.findAll('edit-field')[2].props.children.find(node => node?.type === 'Picker')
  gender.props.onChange({ detail: { value: '2' } })
  await page.flush()
  return { ...page, writes, navigation, toast }
}

for (const kind of ['rejection', 'synchronous throw']) {
  test(`confirmed profile update still navigates when toast has ${kind}`, async () => {
    const failure = { errMsg: 'showToast:fail The "fd" argument must be of type number. Received undefined' }
    const page = await createPage({ showToast: () => {
      if (kind === 'synchronous throw') throw failure
      return Promise.reject(failure)
    } })
    await page.click('primary-button'); await page.flush()
    assert.equal(page.writes.length, 1)
    assert.equal(page.writes[0].gender, 'prefer_not_to_say')
    assert.equal(page.navigation.length, 1)
    assert.equal(page.find('error-banner'), undefined)
    assert.equal(page.text().includes('showToast:fail'), false)
  })
}

test('business save failure keeps input, has no toast/navigation and permits explicit retry', async () => {
  let fails = true
  const page = await createPage({ update: async data => { if (fails) throw new Error('服务暂不可用'); return data } })
  await page.click('primary-button'); await page.flush()
  assert.equal(page.toast.length, 0)
  assert.equal(page.navigation.length, 0)
  assert.ok(page.find('error-banner'))
  assert.ok(page.text().includes('不愿透露'))
  fails = false
  await page.click('primary-button'); await page.flush()
  assert.equal(page.writes.length, 2)
  assert.equal(page.navigation.length, 1)
})

test('failed navigation preserves confirmed status and retry only navigates, never resaves', async () => {
  let fails = true
  const page = await createPage({ navigateBack: async () => { if (fails) throw new Error('native navigation failed') } })
  await page.click('primary-button'); await page.flush()
  assert.ok(page.text().includes('档案已保存'))
  assert.equal(page.find('error-banner'), undefined)
  assert.equal(page.find('primary-button').props.disabled, true)
  fails = false
  await page.click('return-after-save'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.equal(page.navigation.length, 2)
})

test('overlapping save and input while saving cannot repeat writes or mutate confirmed draft', async () => {
  const pending = deferred()
  const page = await createPage({ update: () => pending.promise })
  const first = page.click('primary-button')
  const duplicate = page.click('primary-button')
  page.input('edit-input', '40')
  await page.flush()
  assert.equal(page.writes.length, 1)
  assert.equal(page.findAll('edit-input')[0].props.value, '30')
  pending.resolve({})
  await Promise.all([first, duplicate]); await page.flush()
  assert.equal(page.navigation.length, 1)
})

test('editing after confirmed save clears saved status and permits a new explicit save', async () => {
  const page = await createPage({ navigateBack: async () => { throw new Error('native navigation failed') } })
  await page.click('primary-button'); await page.flush()
  assert.equal(page.find('primary-button').props.disabled, true)
  page.input('edit-input', '31'); await page.flush()
  assert.equal(page.find('primary-button').props.disabled, false)
  assert.equal(page.find('save-notice'), undefined)
  assert.equal(page.find('return-after-save'), undefined)
  await page.click('primary-button'); await page.flush()
  assert.equal(page.writes.length, 2)
  assert.equal(page.writes[1].age, 31)
  assert.equal(page.find('primary-button').props.disabled, true)
})
