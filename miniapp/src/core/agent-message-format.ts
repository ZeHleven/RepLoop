export type AgentTextSpan = { text: string; bold?: boolean; code?: boolean }
export type AgentTextBlock = {
  kind: 'paragraph' | 'heading' | 'list-item' | 'code'
  spans: AgentTextSpan[]
  level?: number
  marker?: string
}

const plain = (text: string): AgentTextBlock[] => [{ kind: 'paragraph', spans: [{ text }] }]

// Deliberately a small text grammar, not HTML or a complete Markdown engine.
// Unsupported syntax stays literal. Never use the result as HTML or an action.
function inline(text: string): AgentTextSpan[] {
  const spans: AgentTextSpan[] = []
  let buffer = ''
  const flush = () => { if (buffer) { spans.push({ text: buffer }); buffer = '' } }
  const closing = (marker: string, start: number) => {
    let pos = text.indexOf(marker, start)
    while (pos >= 0) {
      let escapes = 0
      for (let p = pos - 1; p >= 0 && text[p] === '\\'; p--) escapes++
      if (escapes % 2 === 0 && !/\s/.test(text[pos - 1] || ' ')) return pos
      pos = text.indexOf(marker, pos + marker.length)
    }
    return -1
  }
  for (let i = 0; i < text.length;) {
    if (text[i] === '\\' && /[\\`*#_{}\[\]()!+\-.>~]/.test(text[i + 1] || '')) {
      buffer += text[i + 1]; i += 2; continue
    }
    const marker = text[i] === '`' ? '`' : text.slice(i, i + 2) === '**' ? '**' : ''
    // Do not interpret exponent notation (2**3) as the start of emphasis.
    const canOpen = marker && !/\s/.test(text[i + marker.length] || ' ')
      && (marker === '`' || !/[a-zA-Z0-9_]/.test(text[i - 1] || ''))
    const end = canOpen ? closing(marker, i + marker.length) : -1
    if (end > i + marker.length) {
      flush()
      spans.push({ text: text.slice(i + marker.length, end), ...(marker === '`' ? { code: true } : { bold: true }) })
      i = end + marker.length
    } else {
      buffer += text[i]; i++
    }
  }
  flush()
  return spans.length ? spans : [{ text: '' }]
}

export function parseAgentMessage(source: string): AgentTextBlock[] {
  if (!source) return []
  // Bound both parsing work and UI node count; the fallback preserves ALL text.
  if (source.length > 32000) return plain(source)
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  if (lines.length > 400) return plain(source)
  const blocks: AgentTextBlock[] = []
  let paragraph: string[] = []
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: 'paragraph', spans: inline(paragraph.join('\n')) })
    paragraph = []
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fence = /^\s{0,3}(`{3,}|~{3,})[^`~]*$/.exec(line)
    if (fence) {
      let end = i + 1
      while (end < lines.length && lines[end].trim() !== fence[1]) end++
      if (end === lines.length) {
        flush()
        blocks.push(...plain(lines.slice(i).join('\n')))
        break
      }
      flush()
      blocks.push({ kind: 'code', spans: [{ text: lines.slice(i + 1, end).join('\n'), code: true }] })
      i = end
    } else if (!line.trim()) {
      flush()
    } else {
      const heading = /^ {0,3}(#{1,6})\s+(.+)$/.exec(line)
      const item = /^\s{0,8}([-+*]|\d{1,3}[.)])\s+(.+)$/.exec(line)
      if (heading || item) {
        flush()
        blocks.push(heading
          ? { kind: 'heading', level: heading[1].length, spans: inline(heading[2]) }
          : { kind: 'list-item', marker: /^\d/.test(item![1]) ? item![1] : '•', spans: inline(item![2]) })
      } else paragraph.push(line)
    }
  }
  flush()
  return blocks.reduce((count, block) => count + block.spans.length, 0) > 2000 ? plain(source) : blocks
}
