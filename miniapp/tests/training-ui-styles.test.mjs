import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import * as sass from 'sass'
import postcss from 'postcss'

// Inspect compiled CSS declarations, not source-text matches. Native WeChat
// inputs have a default height, so padding must not consume that content box.
function declarations (file, selector) {
  const css = sass.compile(fileURLToPath(new URL(file, import.meta.url))).css
  const result = {}
  postcss.parse(css).walkRules(rule => {
    if (rule.selectors.includes(selector)) rule.walkDecls(decl => { result[decl.prop] = decl.value })
  })
  return result
}

test('custom single-line inputs have a full-width explicit content-safe height', () => {
  const input = declarations('../src/components/custom-exercise.scss', '.custom-exercise-input')
  assert.equal(input.width, '100%')
  assert.equal(input['box-sizing'], 'border-box')
  assert.equal(input['flex-shrink'], '0')
  assert.equal(input.padding, '0 16px', 'native input must not have vertical padding')
  assert.ok(parseFloat(input.height) >= 88)
  assert.ok(parseFloat(input.height) - 2 > parseFloat(input['font-size']) * Number(input['line-height']))
})

test('archive delete has a compact visible label inside a usable touch target', () => {
  const file = '../src/pages/workouts/index.scss'
  const button = declarations(file, '.delete-archive')
  const label = declarations(file, '.archive-status-chip')
  const group = declarations(file, '.archive-header-actions')
  assert.equal(button.background, 'transparent')
  assert.equal(button.padding, '0')
  assert.ok(parseFloat(button['min-height']) >= 88)
  assert.ok(parseFloat(button['min-width']) >= 88)
  assert.equal(label.padding, '6px 14px')
  assert.equal(label['white-space'], 'nowrap')
  assert.equal(group.display, 'flex')
  assert.equal(group['align-items'], 'center')
  assert.equal(group['flex-shrink'], '0')
  assert.equal(declarations(file, '.plan-heading')['flex-wrap'], 'wrap')
  assert.equal(declarations(file, '.plan-heading-copy')['min-width'], '0')
})

test('delete and archived frames use one explicit chip geometry independent of native button defaults', () => {
  const chip = declarations('../src/pages/workouts/index.scss', '.archive-status-chip')
  assert.equal(chip.display, 'inline-flex')
  assert.equal(chip['align-items'], 'center')
  assert.equal(chip['justify-content'], 'center')
  assert.equal(chip['box-sizing'], 'border-box')
  assert.equal(chip['min-height'], '44px')
  assert.equal(chip.margin, '0')
  assert.equal(chip.padding, '6px 14px')
  assert.equal(chip.border, '0')
  assert.equal(chip['border-radius'], '999px')
  assert.equal(chip.background, '#edf0ed')
  assert.equal(chip['font-size'], '22px')
  assert.equal(chip['font-weight'], '800')
  assert.equal(chip['line-height'], '1.4')
})

test('archive delete uses the delete-plan danger palette without changing chip geometry', () => {
  const file = '../src/pages/workouts/index.scss'
  const label = declarations(file, '.archive-delete-label')
  const planDelete = declarations(file, '.delete-plan-button')
  assert.equal(label.color, planDelete.color)
  assert.equal(label.background, planDelete.background)
  assert.equal(label.color, '#9d382b')
  assert.equal(label.background, '#fff0ed')
  for (const key of ['height', 'min-height', 'padding', 'border', 'border-radius', 'font-size', 'line-height']) {
    assert.equal(label[key], undefined, `delete color must not override shared ${key}`)
  }
})

test('exercise sheet reuses the app palette and isolates scrolling between fixed controls', () => {
  const file = '../src/components/custom-exercise.scss', app = '../src/app.scss'
  const panel = declarations(file, '.exercise-sheet-panel')
  assert.equal(panel.background, declarations(app, 'page').background)
  assert.equal(panel.color, declarations(app, 'page').color)
  assert.equal(panel['border-radius'], '28px 28px 0 0')
  assert.equal(panel.overflow, 'hidden')
  assert.equal(declarations(file, '.exercise-sheet-scroll')['min-height'], '0')
  for (const cls of ['.exercise-sheet-header', '.exercise-sheet-footer']) assert.equal(declarations(file, cls)['flex-shrink'], '0')
  assert.equal(declarations(file, '.exercise-sheet-panel .custom-exercise-input').background, declarations(app, '.card').background)
  assert.match(panel.transition, /^transform /, 'do not animate layout height or all properties')
  assert.match(declarations(file, '.exercise-sheet-backdrop').transition, /^opacity /)
})
