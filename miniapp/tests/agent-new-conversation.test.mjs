import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { runtime } from './helpers/page-runtime.mjs'

const messageComponent = runtime('../../src/components/AgentMessageContent.tsx').exports

// Exercise the real page's event handlers and async continuations. Platform
// dependencies and hook storage are isolated; no backend or native UI is used.
const output = ts.transpileModule(readFileSync(
  new URL('../src/pages/agent/index.tsx', import.meta.url), 'utf8'
), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true
  },
  fileName: 'index.tsx'
}).outputText

function deferred () {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const history = [
  { id: 'old-user', role: 'user', content: '旧问题', content_data: {} },
  { id: 'old-answer', role: 'assistant', content: '旧回答', content_data: {} }
]

function createPage ({ savedId = 'old-conversation', pending = null, api = {}, clock = Date, timer = setTimeout } = {}) {
  const slots = []
  let cursor = 0
  let onLoad
  let onShow
  let onHide
  let tree
  const state = { savedId, pending, modalCalls: 0, submissions: [], runs: [] }
  const storage = {
    getAgentConversationId: () => state.savedId,
    saveAgentConversationId: value => { state.savedId = value },
    clearAgentConversationId: () => { state.savedId = '' },
    getPendingAgentRequest: () => state.pending,
    savePendingAgentRequest: value => { state.pending = value },
    clearPendingAgentRequest: () => { state.pending = null }
  }
  const agentApi = {
    messages: api.messages || (async () => history),
    submit: async (...args) => {
      state.submissions.push(args)
      return api.submit
        ? api.submit(...args)
        : { run_id: 'new-run', conversation_id: 'new-conversation' }
    },
    run: async id => {
      state.runs.push(id)
      return api.run
        ? api.run(id)
        : { id, status: 'completed', reply: '新回答', cards: [] }
    }
  }
  const imports = {
    react: {
      useState: initial => {
        const index = cursor++
        if (!(index in slots)) slots[index] = initial
        return [slots[index], value => {
          slots[index] = typeof value === 'function' ? value(slots[index]) : value
        }]
      },
      useRef: initial => {
        const index = cursor++
        if (!(index in slots)) slots[index] = { current: initial }
        return slots[index]
      },
      useEffect: () => {}
    },
    'react/jsx-runtime': {
      jsx: (type, props) => ({ type, props }),
      jsxs: (type, props) => ({ type, props })
    },
    '@tarojs/components': Object.fromEntries(
      ['Button', 'ScrollView', 'Text', 'Textarea', 'View'].map(name => [name, name])
    ),
    '@tarojs/taro': {
      __esModule: true,
      default: {
        showModal: async () => {
          state.modalCalls++
          throw { errMsg: 'showModal:fail The "fd" argument must be of type number. Received undefined' }
        }
      },
      useLoad: callback => { onLoad = callback },
      useDidShow: callback => { onShow = callback },
      useDidHide: callback => { onHide = callback }
    },
    '../../core/build-info': { miniappBuildLabel: () => 'test-build' },
    // Native navigation is outside this conversation-I/O harness; its real controller
    // and platform integration are covered by capsule-navigation tests and H5 QA.
    '../../core/capsule-platform': {capsuleNavigation:{show:()=>{},keyboard:()=>{}},useCapsuleState:()=>({keyboard:false})},
    '../../components/AgentMessageContent': messageComponent,
    '../../core/proposal-reference': { proposalReferenceFromUnknown: () => null },
    '../../core/proposal-interaction': {},
    '../../core/request': { errorMessage: error => error.message || error.errMsg },
    '../../core/storage': storage,
    '../../services/agent': { agentApi },
    '../../services/proposals': { proposalsApi: {} },
    '../../services/plan-management': { planManagementApi: {} },
    './index.scss': {}
  }
  const loaded = { exports: {} }
  new Function('require', 'exports', 'module', 'Date', 'setTimeout', output)(name => {
    assert.ok(name in imports, `Unexpected import: ${name}`)
    return imports[name]
  }, loaded.exports, loaded, clock, timer)

  function render () {
    cursor = 0
    const expand = node => {
      if (Array.isArray(node)) return node.map(expand)
      if (!node || typeof node !== 'object') return node
      if (typeof node.type === 'function') return expand(node.type(node.props))
      return { ...node, props: { ...node.props, children: expand(node.props?.children) } }
    }
    tree = expand(loaded.exports.default())
  }
  function allNodes (node) {
    if (Array.isArray(node)) return node.flatMap(item => allNodes(item))
    if (!node || typeof node !== 'object') return []
    return [node, ...allNodes(node.props?.children)]
  }
  function find (className) {
    return allNodes(tree).find(node => node.props.className?.split(' ').includes(className))
  }
  render()
  return {
    state,
    find,
    render,
    mount: () => onLoad(),
    show: () => onShow(),
    hide: () => onHide?.(),
    click: className => {
      const target = find(className)
      assert.ok(target, `Missing control: ${className}`)
      return target.props.onClick()
    },
    text: () => JSON.stringify(tree),
    input: value => find('composer-input').props.onInput({ detail: { value } }),
    flush: async () => {
      await new Promise(resolve => setImmediate(resolve))
      render()
    }
  }
}

test('restored assistant history is formatted while user input and saved messages stay literal', async () => {
  const messages = [
    { id: 'u', role: 'user', content: '**我的原文**', content_data: {} },
    { id: 'a', role: 'assistant', content: '## 今天\n\n做 **卧推**。', content_data: {} }
  ]
  const snapshot = JSON.stringify(messages)
  const page = createPage({ api: { messages: async () => messages } })
  page.mount(); await page.flush()
  assert.ok(page.find('agent-text-heading'))
  assert.equal(page.find('agent-text-bold').props.children, '卧推')
  assert.match(page.text(), /\*\*我的原文\*\*/)
  assert.doesNotMatch(page.text(), /## 今天|\*\*卧推\*\*/)
  assert.equal(JSON.stringify(messages), snapshot)
})

test('a recovered completed run uses the same formatter as history, without submitting again', async () => {
  const page = createPage({
    pending: { client_request_id: 'p', message: '旧问题', run_id: 'r', conversation_id: 'old-conversation' },
    api: { run: async () => ({ id: 'r', status: 'completed', reply: '### 恢复回答\n- **重点**', cards: [] }) }
  })
  page.mount(); await page.flush()
  assert.ok(page.find('agent-text-heading'))
  assert.equal(page.find('agent-text-bold').props.children, '重点')
  assert.equal(page.state.submissions.length, 0)
  assert.equal(page.state.pending, null)
})

test('returning to the same conversation resumes an interrupted run', async () => {
  let calls = 0
  const page = createPage({
    pending: { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' },
    api: { run: async () => {
      if (++calls === 1) throw new Error('网络中断')
      return { id: 'old-run', status: 'completed', reply: '恢复成功', cards: [] }
    } }
  })
  page.mount(); await page.flush()
  page.hide(); page.show(); await page.flush()
  assert.equal(calls, 2)
  assert.match(page.text(), /恢复成功/)
  assert.equal(page.state.pending, null)
})

test('load and show overlap uses one submission, and hidden submission resumes using its receipt', async () => {
  const response = deferred()
  const page = createPage({ savedId: '', pending: { client_request_id: 'same', message: '问题' },
    api: { submit: () => response.promise } })
  page.mount(); page.show(); await page.flush()
  assert.equal(page.state.submissions.length, 1)
  page.hide(); page.show(); await page.flush()
  assert.equal(page.state.submissions.length, 1)
  response.resolve({ run_id: 'receipt', conversation_id: 'receipt-conversation' })
  await page.flush(); await page.flush()
  assert.equal(page.state.submissions.length, 1)
  assert.deepEqual(page.state.runs, ['receipt'])
  assert.equal(page.state.pending, null)
})

test('hidden in-flight poll cannot append late results and return starts only one new poll', async () => {
  const response = deferred()
  let calls = 0
  const page = createPage({ pending: { client_request_id: 'p', message: '问题', run_id: 'r', conversation_id: 'old-conversation' },
    api: { run: () => ++calls === 1 ? response.promise : { id: 'r', status: 'completed', reply: '新获取结果', cards: [] } } })
  page.mount(); await page.flush(); page.hide()
  response.resolve({ id: 'r', status: 'completed', reply: '隐藏期间返回', cards: [] })
  await page.flush()
  assert.doesNotMatch(page.text(), /隐藏期间返回/)
  assert.ok(page.state.pending)
  page.show(); page.show(); await page.flush()
  assert.equal(calls, 2)
  assert.match(page.text(), /新获取结果/)
})

test('lost submit response retries with the exact ID and action, never a newly generated request', async () => {
  let calls = 0
  const action = { action: 'save_as_proposal', artifact_id: 'artifact', expected_version: 1, payload_fingerprint: 'f'.repeat(64) }
  const page = createPage({ pending: { client_request_id: 'stable', message: '保存这份方案', artifact_action: action },
    api: { submit: async () => { if (++calls === 1) throw new Error('响应丢失'); return { run_id: 'r', conversation_id: 'old-conversation' } } } })
  page.mount(); await page.flush()
  assert.ok(page.find('resume-agent'))
  await page.click('resume-agent'); await page.flush()
  assert.equal(page.state.submissions.length, 2)
  assert.deepEqual(page.state.submissions[0], page.state.submissions[1])
  assert.equal(page.state.submissions[1][1], 'stable')
  assert.deepEqual(page.state.submissions[1][3], action)
})

test('timeout offers explicit recovery; new input is not silently used to recover old work', async () => {
  let now = 0, calls = 0
  class Clock extends Date { static now () { return now } }
  const page = createPage({ clock: Clock,
    pending: { client_request_id: 'p', message: '旧问题', run_id: 'r', conversation_id: 'old-conversation' },
    api: { run: async () => { now += 180001; calls++; return { id: 'r', status: 'running' } } } })
  page.mount(); await page.flush()
  assert.match(page.text(), /暂时停止等待/)
  assert.ok(page.find('resume-agent'))
  page.input('新的问题'); await page.flush()
  await page.click('send-button'); await page.flush()
  assert.equal(calls, 1)
  assert.equal(page.find('composer-input').props.value, '新的问题')
  assert.match(page.text(), /当前输入已保留/)
})

test('cleared account recovery state cannot be resurrected by a late submission', async () => {
  const response = deferred()
  const page = createPage({ savedId: '', pending: { client_request_id: 'p', message: '问题' }, api: { submit: () => response.promise } })
  page.mount(); await page.flush()
  page.state.pending = null; page.state.savedId = ''
  response.resolve({ run_id: 'r', conversation_id: 'signed-out-conversation' })
  await page.flush()
  assert.equal(page.state.pending, null)
  assert.equal(page.state.savedId, '')
  assert.deepEqual(page.state.runs, [])
})

test('queued status is visible and hiding stops the next scheduled poll', async () => {
  const timers = []
  let calls = 0
  const page = createPage({ timer: callback => { timers.push(callback) },
    pending: { client_request_id: 'p', message: '问题', run_id: 'r', conversation_id: 'old-conversation' },
    api: { run: async () => ++calls === 1 ? { id: 'r', status: 'queued' } : { id: 'r', status: 'completed', reply: '完成', cards: [] } } })
  page.mount(); await page.flush()
  assert.match(page.text(), /正在排队/)
  assert.equal(timers.length, 1)
  page.hide(); timers.shift()(); await page.flush()
  assert.equal(calls, 1)
  page.show(); await page.flush()
  assert.equal(calls, 2); assert.equal(page.state.pending, null)
})

test('sending is blocked during restoration without clearing the typed text', async () => {
  const historyResponse = deferred()
  const page = createPage({ api: { messages: () => historyResponse.promise } })
  page.mount(); page.input('先保留这句话'); await page.flush()
  await page.click('send-button')
  assert.equal(page.state.submissions.length, 0)
  assert.equal(page.find('composer-input').props.value, '先保留这句话')
  historyResponse.resolve(history); await page.flush()
  assert.equal(page.find('send-button').props.disabled, false)
})

test('new conversation works with a broken native modal and submits without the old ID', async () => {
  const page = createPage()
  page.mount()
  await page.flush()
  assert.match(page.text(), /旧回答/)
  page.click('new-chat')
  await page.flush()
  assert.equal(page.state.savedId, '')
  assert.doesNotMatch(page.text(), /旧回答/)
  assert.ok(page.find('quick-prompts'))
  await page.click('quick-prompt')
  await page.flush()
  assert.equal(page.state.submissions.length, 1)
  assert.equal(page.state.submissions[0][2], undefined)
  assert.match(page.text(), /新回答/)
  assert.equal(page.state.modalCalls, 0)
})

test('pending reply uses inline confirmation and continuing to wait preserves the request', async () => {
  const response = deferred()
  const pending = { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' }
  const page = createPage({ pending, api: { run: () => response.promise } })
  page.mount()
  await page.flush()
  page.click('new-chat')
  await page.flush()
  assert.ok(page.find('new-conversation-prompt'))
  assert.equal(page.state.pending, pending)
  page.click('keep-conversation')
  await page.flush()
  assert.equal(page.find('new-conversation-prompt'), undefined)
  assert.equal(page.state.savedId, 'old-conversation')
  response.resolve({ id: 'old-run', status: 'completed', reply: '等待后完成', cards: [] })
  await page.flush()
  assert.match(page.text(), /等待后完成/)
  assert.equal(page.state.modalCalls, 0)
})

test('a late submission cannot restore an abandoned conversation or unlock a newer request', async () => {
  const first = deferred()
  const second = deferred()
  let calls = 0
  const page = createPage({ savedId: '', api: { submit: () => ++calls === 1 ? first.promise : second.promise } })
  page.mount()
  await page.flush()
  const firstSend = page.click('quick-prompt')
  await page.flush()
  page.click('new-chat')
  await page.flush()
  page.click('confirm-new-conversation')
  await page.flush()
  const secondSend = page.click('quick-prompt')
  const currentPending = page.state.pending
  first.resolve({ run_id: 'old-run', conversation_id: 'old-conversation' })
  await firstSend
  // Use the same not-yet-rerendered handler to exercise the synchronous send lock.
  await page.click('quick-prompt')
  assert.equal(page.state.submissions.length, 2)
  assert.equal(page.state.savedId, '')
  assert.equal(page.state.pending, currentPending)
  assert.deepEqual(page.state.runs, [])
  second.resolve({ run_id: 'new-run', conversation_id: 'new-conversation' })
  await secondSend
  await page.flush()
  assert.equal(page.state.savedId, 'new-conversation')
  assert.deepEqual(page.state.runs, ['new-run'])
  assert.equal(page.state.modalCalls, 0)
})

test('cancelled history restoration cannot restart its saved pending request', async () => {
  const response = deferred()
  const page = createPage({
    pending: { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' },
    api: { messages: () => response.promise }
  })
  page.mount()
  page.click('new-chat')
  await page.flush()
  page.click('confirm-new-conversation')
  response.resolve(history)
  await page.flush()
  assert.equal(page.state.savedId, '')
  assert.equal(page.state.pending, null)
  assert.deepEqual(page.state.runs, [])
  assert.doesNotMatch(page.text(), /旧回答|新回答/)
})

for (const fail of [false, true]) {
  test(`late ${fail ? 'failed' : 'successful'} history loading cannot replace a new conversation`, async () => {
    const response = deferred()
    const page = createPage({ api: { messages: () => response.promise } })
    page.mount()
    page.click('new-chat')
    await page.flush()
    if (fail) response.reject(new Error('旧会话加载失败'))
    else response.resolve(history)
    await page.flush()
    assert.equal(page.state.savedId, '')
    assert.doesNotMatch(page.text(), /旧回答|旧会话加载失败/)
    assert.equal(page.find('loading-state'), undefined)
    assert.ok(page.find('quick-prompts'))
  })

  test(`late ${fail ? 'failed' : 'successful'} run responses cannot affect a new conversation`, async () => {
    const response = deferred()
    const page = createPage({
      pending: { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' },
      api: { run: () => response.promise }
    })
    page.mount()
    await page.flush()
    page.click('new-chat')
    await page.flush()
    page.click('confirm-new-conversation')
    await page.flush()
    if (fail) response.reject(new Error('旧请求错误'))
    else response.resolve({ id: 'old-run', status: 'completed', reply: '过时回答', cards: [] })
    await page.flush()
    assert.equal(page.state.savedId, '')
    assert.equal(page.state.pending, null)
    assert.doesNotMatch(page.text(), /过时回答|旧请求错误/)
    assert.equal(page.find('typing'), undefined)
    assert.ok(page.find('quick-prompts'))
  })
}
