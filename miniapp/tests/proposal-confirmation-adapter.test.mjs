import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import ts from 'typescript'


function loadTypeScriptModule (relativePath) {
  const sourceUrl = new URL(relativePath, import.meta.url)
  const sourcePath = fileURLToPath(sourceUrl)
  const output = ts.transpileModule(readFileSync(sourcePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: sourcePath
  }).outputText
  const loaded = { exports: {} }
  const evaluate = new Function('exports', 'module', output)
  evaluate(loaded.exports, loaded)
  return loaded.exports
}

const runtime = loadTypeScriptModule('../src/core/proposal-confirmation.ts')


test('native WeChat modal is authoritative when it succeeds', async () => {
  let taroCalls = 0
  const result = await runtime.requestProposalConfirmation(
    runtime.proposalConfirmationOptions('confirm', 1),
    {
      native: options => options.success({
        confirm: true,
        cancel: false,
        errMsg: 'showModal:ok'
      }),
      taro: async () => {
        taroCalls += 1
        return { confirm: false, cancel: true }
      }
    }
  )

  assert.deepEqual(result, {
    choice: 'accept',
    channel: 'native',
    diagnostics: []
  })
  assert.equal(taroCalls, 0)
})

test('native failure falls back once to Taro without accepting optimistically', async () => {
  const result = await runtime.requestProposalConfirmation(
    runtime.proposalConfirmationOptions('reject', 2),
    {
      native: options => options.fail({
        errMsg: 'showModal:fail invalid argument',
        detail: 'must not be logged'
      }),
      taro: async () => ({ confirm: false, cancel: true })
    }
  )

  assert.deepEqual(result, {
    choice: 'cancel',
    channel: 'taro',
    diagnostics: [{
      channel: 'native',
      category: 'invalid_argument',
      error_type: 'object',
      error_code: null
    }]
  })
  assert.doesNotMatch(JSON.stringify(result), /must not be logged/u)
})

test('legacy fail-cancel is treated as cancellation and never retried', async () => {
  let taroCalls = 0
  const result = await runtime.requestProposalConfirmation(
    runtime.proposalConfirmationOptions('confirm', 1),
    {
      native: options => options.fail({ errMsg: 'showModal:fail cancel' }),
      taro: async () => {
        taroCalls += 1
        return { confirm: true, cancel: false }
      }
    }
  )

  assert.equal(result.choice, 'cancel')
  assert.equal(result.channel, 'native')
  assert.equal(taroCalls, 0)
})

test('two unavailable modal paths degrade to explicit inline confirmation', async () => {
  const result = await runtime.requestProposalConfirmation(
    runtime.proposalConfirmationOptions('confirm', 3),
    {
      native: () => {
        throw new TypeError('showModal is not a function')
      },
      taro: async () => {
        throw { code: 'INTERNAL_SECRET_NOT_SAFE', errMsg: 'invoke fail' }
      }
    }
  )

  assert.equal(result.choice, 'inline_required')
  assert.equal(result.channel, 'inline')
  assert.deepEqual(result.diagnostics, [
    {
      channel: 'native',
      category: 'api_unavailable',
      error_type: 'TypeError',
      error_code: null
    },
    {
      channel: 'taro',
      category: 'invocation_failed',
      error_type: 'object',
      error_code: null
    }
  ])
  assert.doesNotMatch(JSON.stringify(result), /SECRET/u)
})

test('missing platform APIs cannot authorize a proposal mutation', async () => {
  const result = await runtime.requestProposalConfirmation(
    runtime.proposalConfirmationOptions('confirm', 1),
    {}
  )

  assert.deepEqual(result, {
    choice: 'inline_required',
    channel: 'inline',
    diagnostics: []
  })
})

test('confirmation copy stays action-specific and bounded', () => {
  const confirm = runtime.proposalConfirmationOptions('confirm', 4)
  const reject = runtime.proposalConfirmationOptions('reject', 4)

  assert.equal(confirm.confirmText, '确认应用')
  assert.match(confirm.content, /4 项变化/u)
  assert.equal(reject.confirmText, '确认拒绝')
  assert.match(reject.content, /不会修改当前训练计划/u)
  assert.equal(confirm.showCancel, true)
  assert.equal(reject.showCancel, true)
})
