import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

// Real TSX handlers with keyed component hook state and effect cleanup. Only I/O,
// the native host components and the React scheduling boundary are substituted.
export function interactionRuntime (relative, overrides = {}, globals = {}) {
  const fibers = new Map(), modules = new Map(), pendingEffects = []
  let current, tree, dirty = false, props = {}, mounted = true
  const hooks = {}, hook = name => fn => { hooks[name] = fn }
  const imports = {
    react: {
      useState (initial) {
        const fiber = current, index = fiber.cursor++
        if (!(index in fiber.slots)) fiber.slots[index] = typeof initial === 'function' ? initial() : initial
        return [fiber.slots[index], next => {
          if (!fiber.mounted) return
          const value = typeof next === 'function' ? next(fiber.slots[index]) : next
          if (!Object.is(value, fiber.slots[index])) { fiber.slots[index] = value; dirty = true }
        }]
      },
      useRef (initial) {
        const index = current.cursor++
        if (!(index in current.slots)) current.slots[index] = { current: initial }
        return current.slots[index]
      },
      useEffect (callback, deps) {
        const fiber = current, index = fiber.cursor++, prior = fiber.slots[index]
        if (!prior || !deps || deps.length !== prior.deps?.length || deps.some((v, i) => !Object.is(v, prior.deps[i]))) {
          const effect = { deps, cleanup: prior?.cleanup }
          fiber.slots[index] = effect
          pendingEffects.push(() => {
            effect.cleanup?.()
            if (fiber.mounted) effect.cleanup = callback()
          })
        }
      }
    },
    'react/jsx-runtime': Object.fromEntries(['jsx', 'jsxs'].map(k => [k, (type, props, key) => ({ type, props, key })])),
    '@tarojs/components': Object.fromEntries(['View', 'Text', 'Button', 'Input', 'Picker', 'Switch', 'Textarea', 'ScrollView', 'Slider', 'RootPortal', 'PageMeta', 'NavigationBar'].map(k => [k, k])),
    '@tarojs/taro': { __esModule: true, default: {}, ...Object.fromEntries(['useLoad', 'useDidShow', 'useDidHide', 'useUnload'].map(k => [k, hook(k)])) },
    ...overrides
  }
  function load (filename) {
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const code = ts.transpileModule(readFileSync(filename, 'utf8'), {
      fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true }
    }).outputText
    new Function('require', 'exports', 'module', ...Object.keys(globals), code)(name => {
      if (name in imports) return imports[name]
      if (name.endsWith('.scss')) return {}
      if (name.endsWith('.svg')) return name
      assert.ok(name.startsWith('.'), `Unexpected dependency: ${name}`)
      const base = path.resolve(path.dirname(filename), name)
      const target = ['.ts', '.tsx'].map(ext => base + ext).find(existsSync)
      assert.ok(target, `Missing source: ${base}`)
      return load(target)
    }, module.exports, module, ...Object.values(globals))
    return module.exports
  }
  const loadSource = relative => load(fileURLToPath(new URL(relative, import.meta.url)))
  const exports = loadSource(relative)
  const cleanup = fiber => {
    fiber.mounted = false
    for (const slot of fiber.slots) slot?.cleanup?.()
  }
  function expand (node, key, seen) {
    if (Array.isArray(node)) return node.map((child, i) => expand(child, `${key}/${child?.key ?? i}`, seen))
    if (!node || typeof node !== 'object') return node
    if (typeof node.type === 'function') {
      let fiber = fibers.get(key)
      if (fiber?.type !== node.type) {
        if (fiber) cleanup(fiber)
        fiber = { type: node.type, cursor: 0, slots: [], mounted: true }; fibers.set(key, fiber)
      }
      seen.add(key); current = fiber; fiber.cursor = 0
      return expand(node.type(node.props), `${key}/render`, seen)
    }
    return { ...node, props: { ...node.props, children: expand(node.props?.children, `${key}/children`, seen) } }
  }
  function render (nextProps = props) {
    if (!mounted) return
    props = nextProps; dirty = false
    const seen = new Set()
    tree = expand({ type: exports.default, props }, 'root', seen)
    for (const [key, fiber] of fibers) if (!seen.has(key)) { cleanup(fiber); fibers.delete(key) }
    while (pendingEffects.length) pendingEffects.shift()()
  }
  const nodes = node => Array.isArray(node) ? node.flatMap(nodes) : node && typeof node === 'object' ? [node, ...nodes(node.props?.children)] : []
  const findAll = cls => nodes(tree).filter(node => node.props?.className?.split(' ').includes(cls))
  const find = cls => findAll(cls)[0]
  const invoke = (cls, event, value, index = 0) => {
    const node = findAll(cls)[index]; assert.ok(node, `Missing ${cls}`)
    return node.props[event]?.(value)
  }
  return {
    exports, loadSource, hooks, render, find, findAll, invoke,
    click: (cls, index = 0) => invoke(cls, 'onClick', {}, index),
    input: (cls, value, index = 0) => invoke(cls, 'onInput', { detail: { value } }, index),
    text: () => JSON.stringify(tree),
    async flush () {
      await new Promise(resolve => setImmediate(resolve)); render()
      for (let count = 0; dirty && count < 20; count++) render()
      assert.equal(dirty, false, 'Unstable render loop')
    },
    unmount () { mounted = false; for (const fiber of fibers.values()) cleanup(fiber); fibers.clear() }
  }
}

export function manualClock (initial = 100000) {
  let time = initial, next = 0
  const timers = new Map()
  class Clock extends Date { static now () { return time } }
  const schedule = (fn, delay, repeat) => { const id = ++next; timers.set(id, { fn, due: time + delay, repeat }); return id }
  return {
    globals: { Date: Clock, setTimeout: (fn, ms) => schedule(fn, ms, 0), clearTimeout: id => timers.delete(id),
      setInterval: (fn, ms) => schedule(fn, ms, ms), clearInterval: id => timers.delete(id) },
    count: () => timers.size,
    advance (ms) {
      const target = time + ms
      for (;;) {
        const entry = [...timers].filter(([, t]) => t.due <= target).sort((a, b) => a[1].due - b[1].due)[0]
        if (!entry) break
        const [id, timer] = entry; time = timer.due
        if (timer.repeat) timer.due += timer.repeat; else timers.delete(id)
        timer.fn()
      }
      time = target
    }
  }
}
