import assert from 'node:assert/strict'
import test from 'node:test'
import { interactionRuntime, manualClock } from './helpers/interaction-runtime.mjs'
import { deferred } from './helpers/page-runtime.mjs'

const errors = { errorMessage: e => e.message }
function fixture (api = {}) {
  const clock = manualClock(), hooks = {}, listeners = new Set(), reads = [], writes = [], added = [], visibility = [], drafts = []
  const page = interactionRuntime('../../src/components/CustomExerciseEntry.tsx', {
    '../core/request': errors,
    '../services/exercises': { exerciseApi: {
      custom: async () => { reads.push('read'); return [] },
      createCustom: async body => { writes.push(body); return { exercise_id: 'created', exercise_name: body.name } }, ...api
    } },
    '@tarojs/taro': { __esModule: true,
      useDidHide: fn => { hooks.hide = fn }, useUnload: fn => { hooks.unload = fn },
      default: {
        nextTick: fn => queueMicrotask(fn), getWindowInfo: () => ({ windowHeight: 760 }),
        onKeyboardHeightChange: fn => listeners.add(fn), offKeyboardHeightChange: fn => listeners.delete(fn),
        hideKeyboard: async () => {}, pageScrollTo: () => { throw new Error('The background page must not be scrolled') }
      }
    }
  }, clock.globals)
  page.render({ dayLabel: '周二', onAdd: row => added.push(row), onOpenChange: value => visibility.push(value), onDraftChange: value => drafts.push(value) })
  return { ...page, clock, hooks, listeners, reads, writes, added, visibility, drafts }
}
async function open (page) { await page.click('open-custom-exercise'); await page.flush(); page.clock.advance(30); await page.flush() }
async function close (page) { await page.click('close-custom-exercise'); await page.flush(); page.clock.advance(260); await page.flush() }

test('custom form opens in a portal with separate scroll area and stable trigger; opening writes nothing', async () => {
  const page = fixture(); await open(page)
  assert.ok(page.find('open-custom-exercise'), 'opening must not remove the page anchor')
  assert.equal(page.find('exercise-sheet-portal').type, 'RootPortal')
  assert.equal(page.find('exercise-sheet-scroll').type, 'ScrollView')
  assert.ok(page.find('exercise-sheet-header')); assert.ok(page.find('exercise-sheet-footer'))
  assert.match(page.text(), /周二/)
  assert.deepEqual(page.visibility, [true]); assert.equal(page.writes.length, 0)
  page.unmount(); assert.equal(page.clock.count(), 0)
})

test('closing and reopening preserves fields, keeps background fixed and performs zero writes', async () => {
  const page = fixture(); await open(page)
  page.input('custom-exercise-name', '弹力带划船'); page.input('custom-exercise-description', '分组练习'); await page.flush()
  assert.equal(page.drafts.at(-1), true)
  await close(page)
  assert.equal(page.find('exercise-sheet-layer').props.style.display, 'none')
  assert.equal(page.visibility.at(-1), false)
  await open(page)
  assert.equal(page.find('custom-exercise-name').props.value, '弹力带划船')
  assert.equal(page.find('custom-exercise-description').props.value, '分组练习')
  assert.equal(page.writes.length, 0); assert.equal(page.added.length, 0)
  page.unmount()
})

test('rapid toggles and late reads cannot reopen a dismissed sheet or leave an animation timer', async () => {
  const request = deferred(); const page = fixture({ custom: () => request.promise })
  const first = page.click('open-custom-exercise'); const duplicate = page.click('open-custom-exercise')
  await page.flush(); await close(page)
  request.resolve([]); await first; await duplicate; await page.flush()
  assert.equal(page.find('exercise-sheet-layer').props.style.display, 'none')
  assert.equal(page.clock.count(), 0)
  assert.deepEqual(page.visibility, [true, false]); page.unmount()
})

test('backgrounding immediately releases overlay and keyboard listener but preserves the draft', async () => {
  const page = fixture(); await open(page)
  page.input('custom-exercise-name', '保留草稿'); await page.flush()
  assert.equal(page.listeners.size, 1)
  page.hooks.hide(); await page.flush(); page.clock.advance(2000); await page.flush()
  assert.equal(page.find('exercise-sheet-layer').props.style.display, 'none')
  assert.equal(page.visibility.at(-1), false); assert.equal(page.listeners.size, 0); assert.equal(page.clock.count(), 0)
  await open(page); assert.equal(page.find('custom-exercise-name').props.value, '保留草稿'); page.unmount()
})

test('keyboard resize makes room inside the sheet without pushing the page', async () => {
  const page = fixture(); await open(page)
  for (const callback of page.listeners) callback({ height: 300 })
  await page.flush()
  assert.equal(page.find('exercise-sheet-panel').props.style.bottom, '300px')
  assert.ok(parseFloat(page.find('exercise-sheet-panel').props.style.height) <= 444)
  assert.equal(page.find('custom-exercise-name').props.adjustPosition, false)
  assert.equal(page.find('custom-exercise-description').props.adjustPosition, false)
  await close(page); assert.equal(page.listeners.size, 0); page.unmount()
})

test('save failure keeps the panel and all inputs; successful double-click submits and adds once', async () => {
  const request = deferred(); let tries = 0
  const page = fixture({ createCustom: async body => { tries++; if (tries === 1) throw new Error('保存失败'); return request.promise } })
  await open(page); page.input('custom-exercise-name', '划船'); page.input('custom-exercise-description', '常规训练'); await page.flush()
  await page.click('create-custom-exercise'); await page.flush()
  assert.match(page.text(), /保存失败/); assert.equal(page.find('custom-exercise-name').props.value, '划船')
  const first = page.click('create-custom-exercise'); const second = page.click('create-custom-exercise'); await page.flush()
  await page.click('close-custom-exercise'); await page.flush()
  assert.equal(page.visibility.at(-1), true, 'cannot close while a save is unresolved')
  request.resolve({ exercise_id: 'created', exercise_name: '划船' }); await first; await second; await page.flush()
  page.clock.advance(260); await page.flush()
  assert.equal(tries, 2); assert.equal(page.added.length, 1); assert.equal(page.visibility.at(-1), false)
  assert.equal(page.drafts.at(-1), false); page.unmount()
})

test('library classification draft survives closing and its save keeps the panel locked until settled', async () => {
  const request = deferred(), original = { exercise_id: 'existing', exercise_name: '弹力带划船', energy_category: null, energy_category_version: 0 }
  let writes = 0
  const page = fixture({ custom: async () => [original], updateEnergyCategory: () => { writes++; return request.promise } })
  await open(page)
  page.invoke('library-exercise-picker', 'onChange', { detail: { value: '1' } }); await page.flush()
  page.invoke('library-energy-picker', 'onChange', { detail: { value: '1' } }); await page.flush()
  assert.equal(page.drafts.at(-1), true)
  await close(page); await open(page)
  assert.equal(page.find('library-energy-picker').props.value, 1)
  const saving = page.click('save-library-energy'); await page.flush()
  await page.click('close-custom-exercise'); await page.flush()
  assert.equal(page.visibility.at(-1), true)
  request.resolve({ ...original, energy_category: 'resistance_training', energy_category_version: 1 })
  await saving; await page.flush()
  assert.equal(writes, 1); assert.equal(page.drafts.at(-1), false)
  assert.equal(page.added.length, 0, 'classification editing must not add an exercise to a plan')
  await close(page); page.unmount()
})

test('unmount cancels transition callbacks; a late create response cannot mutate another page draft', async () => {
  const request = deferred(), page = fixture({ createCustom: () => request.promise })
  await open(page); page.input('custom-exercise-name', '测试'); page.input('custom-exercise-description', '方法'); await page.flush()
  const saving = page.click('create-custom-exercise'); await page.flush()
  page.unmount(); request.resolve({ exercise_id: 'late', exercise_name: '测试' }); await saving
  page.clock.advance(5000)
  assert.equal(page.added.length, 0); assert.equal(page.listeners.size, 0); assert.equal(page.clock.count(), 0)
})

test('late keyboard callbacks after close cannot move the hidden sheet', async () => {
  const page = fixture(); await open(page)
  const callback = [...page.listeners][0]
  await close(page); callback({ height: 320 }); await page.flush()
  assert.equal(page.find('exercise-sheet-panel').props.style.bottom, '0px')
  page.unmount()
})
