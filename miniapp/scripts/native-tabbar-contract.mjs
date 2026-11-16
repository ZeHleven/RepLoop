import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { parse } = createRequire(require.resolve('webpack/package.json'))('acorn')

function walk(node, visit) {
  if (!node || typeof node !== 'object') return
  if (typeof node.type === 'string') visit(node)
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(item => walk(item, visit))
    else if (value && typeof value === 'object') walk(value, visit)
  }
}

// Inspect actual Webpack modules, not source-only imports or a browser adapter.
// A native Component entry must first execute when its own host loads it.
export function validateNativeTabbar({ javascriptFiles, appConfig }) {
  if (!appConfig.tabBar?.custom) return []
  const modules = new Map(), entries = new Map()
  for (const file of javascriptFiles) {
    const roots = []
    walk(parse(file.content, { ecmaVersion: 'latest' }), node => {
      if (node.type !== 'CallExpression' || node.callee.type !== 'MemberExpression' || node.callee.property.name !== 'push') return
      const tuple = node.arguments[0]?.elements
      if (tuple?.[1]?.type !== 'ObjectExpression') return
      for (const property of tuple[1].properties) {
        if (property.value?.type !== 'FunctionExpression') continue
        const factory = property.value, dependencies = [], loader = factory.params[2]?.name
        let registersComponent = false
        walk(factory.body, child => {
          if (child.type !== 'CallExpression') return
          if (child.callee.name === 'Component') registersComponent = true
          if (loader && child.callee.name === loader && child.arguments[0]?.type === 'Literal') dependencies.push(String(child.arguments[0].value))
        })
        modules.set(String(property.key.value ?? property.key.name), { dependencies, registersComponent })
      }
      walk(tuple[2], child => {
        if (child.type === 'CallExpression' && child.callee.type === 'Identifier' && child.arguments[0]?.type === 'Literal') roots.push(String(child.arguments[0].value))
      })
    })
    entries.set(file.path, roots)
  }
  const native = entries.get('custom-tab-bar/index.js') || []
  if (native.length !== 1 || !modules.get(native[0])?.registersComponent) {
    return [{ code: 'native_tabbar_entry_missing', message: 'Custom tab bar must have its own compiled Component registration entry.' }]
  }
  const pending = [...(entries.get('app.js') || [])], visited = new Set()
  if (!pending.length) return [{ code: 'native_tabbar_app_entry_missing', message: 'Cannot verify App entry isolation.' }]
  while (pending.length) {
    const id = pending.pop()
    if (visited.has(id)) continue
    visited.add(id)
    if (native.includes(id)) return [{ code: 'native_tabbar_eager_import', message: 'App imports the native custom-tab-bar entry before its host; Component registration would be consumed by the module cache.' }]
    pending.push(...(modules.get(id)?.dependencies || []))
  }
  return []
}
