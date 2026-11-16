import assert from 'node:assert/strict'
import test from 'node:test'
import { performance } from 'node:perf_hooks'
import { runtime } from './helpers/page-runtime.mjs'

const { parseAgentMessage } = runtime('../../src/core/agent-message-format.ts').exports
const text = blocks => blocks.map(block => block.spans.map(span => span.text).join('')).join('\n')

test('headings, emphasis and lists are semantic blocks, not visible Markdown markers', () => {
  const blocks = parseAgentMessage('## 今天的安排\n\n先做 **卧推**。\n\n### 注意\n- 休息 **120 秒**\n1. 保持动作稳定')
  assert.deepEqual(blocks.map(b => b.kind), ['heading', 'paragraph', 'heading', 'list-item', 'list-item'])
  assert.equal(blocks[0].level, 2)
  assert.equal(blocks[3].marker, '•')
  assert.equal(blocks[4].marker, '1.')
  assert.equal(blocks[1].spans.find(s => s.bold).text, '卧推')
  assert.equal(text(blocks), '今天的安排\n先做 卧推。\n注意\n休息 120 秒\n保持动作稳定')
})

test('literal symbols, decimals, units, unmatched emphasis and escaped markers survive', () => {
  const source = 'C#、3 * 8、1.5 kg、2**3、**未闭合\n\\# 不是标题\n\\*\\*原样星号\\*\\*'
  const blocks = parseAgentMessage(source)
  assert.equal(blocks.length, 1)
  assert.equal(text(blocks), 'C#、3 * 8、1.5 kg、2**3、**未闭合\n# 不是标题\n**原样星号**')
})

test('inline and fenced code preserve all symbols and no HTML is interpreted', () => {
  const blocks = parseAgentMessage('示例：`**不是加粗**`\n\n```text\n## 原样\n<script>alert(1)</script>\n```\n\n[网站](javascript:alert(1))')
  assert.equal(blocks[0].spans.find(s => s.code).text, '**不是加粗**')
  assert.equal(blocks[1].kind, 'code')
  assert.equal(text([blocks[1]]), '## 原样\n<script>alert(1)</script>')
  assert.equal(text([blocks[2]]), '[网站](javascript:alert(1))')
})

test('unclosed fence and empty input degrade without losing content', () => {
  assert.deepEqual(parseAgentMessage(''), [])
  const source = '```text\n## 未闭合'
  assert.equal(text(parseAgentMessage(source)), source)
})

test('oversized or delimiter-heavy content is bounded and never silently truncated', () => {
  for (const source of ['**'.repeat(40000), '一\n'.repeat(2000), '🧘'.repeat(20000)]) {
    const start = performance.now()
    const blocks = parseAgentMessage(source)
    assert.equal(blocks.length, 1)
    assert.equal(text(blocks), source)
    assert.ok(performance.now() - start < 1000)
  }
})

test('seeded arbitrary Unicode and punctuation inputs never throw or mutate their source', () => {
  let seed = 539
  const alphabet = ['中', '🙂', '*', '#', '\\', '`', '[', ']', '<', '>', '\n', ' ', '1', '.', '\u0000', '\u202e']
  const samples = new Set()
  for (let sample = 0; sample < 300; sample++) {
    let source = ''
    for (let i = 0; i < 240; i++) {
      seed = (1664525 * seed + 1013904223) >>> 0
      source += alphabet[(seed >>> 16) % alphabet.length]
    }
    const original = source
    samples.add(source)
    const blocks = parseAgentMessage(source)
    assert.ok(blocks.every(b => b.spans.every(s => typeof s.text === 'string')))
    assert.equal(source, original)
  }
  assert.equal(samples.size, 300)
})

test('the real component renders text only, including hostile HTML and links', () => {
  const page = runtime('../../src/components/AgentMessageContent.tsx')
  const content = '## 安排\n\n先做 **卧推**。\n\n- 休息120秒\n\n<img src=x onerror=alert(1)>\n[点我](javascript:alert(1))'
  page.render({ content })
  assert.equal(page.findAll('agent-text-heading').length, 1)
  assert.equal(page.findAll('agent-text-bold').length, 1)
  assert.equal(page.find('agent-text-bold').props.children, '卧推')
  assert.equal(page.findAll('agent-block-text').every(n => n.props.selectable), true)
  assert.match(page.text(), /<img src=x onerror=alert\(1\)>/)
  assert.doesNotMatch(page.text(), /"(?:onClick|src|href|dangerouslySetInnerHTML)":/)
  assert.doesNotMatch(page.text(), /"type":"(?:RichText|WebView|Image|Navigator)"/)
  assert.doesNotMatch(page.text(), /## 安排|\*\*卧推\*\*/)
})
