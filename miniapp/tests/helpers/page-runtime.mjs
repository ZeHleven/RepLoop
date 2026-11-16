import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

export const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

// Execute actual TS/TSX, replacing only hooks, platform APIs and explicit I/O.
export function runtime (relative, overrides = {}, globals = {}) {
  const slots = []
  let cursor = 0, tree
  const hooks = {}
  const effects = []
  const imports = {
    react: {
      memo: component => component,
      useState: initial => {
        const i = cursor++
        if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial
        return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value }]
      },
      useRef: initial => {
        const i = cursor++
        if (!(i in slots)) slots[i] = { current: initial }
        return slots[i]
      },
      useEffect: (callback, deps) => {
        const i = cursor++
        if (!(i in slots) || !deps || deps.some((v, n) => v !== slots[i]?.[n])) {
          slots[i] = deps
          effects.push(callback)
        }
      }
    },
    'react/jsx-runtime': {
      jsx: (type, props, key) => ({ type, props, key }),
      jsxs: (type, props, key) => ({ type, props, key })
    },
    '@tarojs/components': Object.fromEntries(['View', 'Text', 'Button', 'Input', 'Picker', 'Textarea', 'ScrollView', 'PageMeta', 'RootPortal', 'NavigationBar'].map(x => [x, x])),
    '@tarojs/taro': {
      __esModule: true,
      default: { pageScrollTo: async () => {}, showToast: async () => {}, nextTick: callback => { queueMicrotask(callback) } },
      ...Object.fromEntries(['useLoad', 'useDidShow', 'useDidHide'].map(x => [x, callback => { hooks[x] = callback }]))
    },
    ...overrides
  }
  const cache = new Map()
  function load (filename) {
    if (cache.has(filename)) return cache.get(filename).exports
    const output = ts.transpileModule(readFileSync(filename, 'utf8'), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true }
    }).outputText
    const module = { exports: {} }
    cache.set(filename, module)
    new Function('require', 'exports', 'module', ...Object.keys(globals), output)(name => {
      if (name in imports) return imports[name]
      if (name.endsWith('.scss')) return {}
      if (name.endsWith('.svg')) return name
      assert.ok(name.startsWith('.'), `Unexpected dependency: ${name}`)
      const base = path.resolve(path.dirname(filename), name)
      const target = ['.ts', '.tsx'].map(ext => base + ext).find(existsSync)
      assert.ok(target, `Missing TS module: ${base}`)
      return load(target)
    }, module.exports, module, ...Object.values(globals))
    return module.exports
  }
  const exports = load(fileURLToPath(new URL(relative, import.meta.url)))
  const nodes = value => Array.isArray(value)
    ? value.flatMap(nodes)
    : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : []
  const findAll = cls => nodes(tree).filter(x => x.props?.className?.split(' ').includes(cls))
  const find = cls => findAll(cls)[0]
  const expand = node => {
    if (Array.isArray(node)) return node.map(expand)
    if (!node || typeof node !== 'object') return node
    if (typeof node.type === 'function') return expand(node.type(node.props))
    return { ...node, props: { ...node.props, children: expand(node.props?.children) } }
  }
  const render = (props = {}) => { cursor = 0; tree = expand(exports.default(props)); while (effects.length) effects.shift()() }
  return {
    exports, hooks, find, findAll, render,
    loadSource: relative => load(fileURLToPath(new URL(relative, import.meta.url))),
    text: () => JSON.stringify(tree),
    click: cls => { assert.ok(find(cls), `Missing ${cls}`); return find(cls).props.onClick() },
    input: (cls, value, index = 0) => {
      const target = findAll(cls)[index]
      assert.ok(target, `Missing ${cls}[${index}]`)
      return target.props.onInput({ detail: { value } })
    },
    flush: async () => { await new Promise(resolve => setImmediate(resolve)); render() }
  }
}
