import assert from 'node:assert/strict'
import test from 'node:test'
import { interactionRuntime, manualClock } from './helpers/interaction-runtime.mjs'

function scope () {
  const clock = manualClock(), history = []
  const loaded = interactionRuntime('../../src/core/interaction-motion.ts', {}, clock.globals)
  const motion = loaded.exports.createMotionScope(value => history.push(value))
  return { motion, clock, history }
}

test('new feedback replaces an old timer instead of replaying stale completion', () => {
  const { motion, clock } = scope(), token = motion.capture()
  motion.pulse('row', token, 600); clock.advance(400)
  motion.pulse('row', token, 600); assert.equal(clock.count(), 1)
  clock.advance(201); assert.deepEqual(motion.snapshot().active, ['row'])
  clock.advance(399); assert.deepEqual(motion.snapshot().active, [])
  assert.equal(clock.count(), 0)
})

test('hide cancels all presentation work and invalidates receipts even after showing again', () => {
  const { motion, clock } = scope(), token = motion.capture()
  motion.pulse('row', token); motion.pulse('panel', token)
  motion.setVisible(false); assert.equal(clock.count(), 0)
  assert.equal(motion.isVisible(), false)
  motion.setVisible(true)
  assert.equal(motion.pulse('late', token), false)
  assert.deepEqual(motion.snapshot().active, [])
  assert.equal(motion.pulse('new', motion.capture()), true)
  motion.dispose(); assert.equal(clock.count(), 0)
})

test('repeated show notifications do not restart feedback or discard its expiration', () => {
  const { motion, clock } = scope(), token = motion.capture()
  motion.pulse('row', token)
  clock.advance(300); motion.setVisible(true); motion.setVisible(true)
  assert.equal(clock.count(), 1); assert.equal(motion.capture(), token)
  clock.advance(300)
  assert.deepEqual(motion.snapshot().active, [])
})

test('cancelling one target leaves unrelated feedback intact', () => {
  const { motion, clock } = scope(), token = motion.capture()
  motion.pulse('row', token); motion.pulse('panel', token)
  motion.cancel('row'); assert.deepEqual(motion.snapshot().active, ['panel'])
  assert.equal(clock.count(), 1); motion.dispose(); assert.equal(clock.count(), 0)
})

test('unmount prevents late notifications; reactivation still rejects old receipts', () => {
  const { motion, clock, history } = scope(), token = motion.capture()
  motion.pulse('row', token); const before = history.length
  motion.dispose(); clock.advance(1000)
  assert.equal(history.length, before); assert.equal(motion.pulse('late', token), false)
  motion.activate(); assert.equal(motion.pulse('late', token), false)
  assert.equal(motion.pulse('new', motion.capture()), true)
  motion.dispose()
})
