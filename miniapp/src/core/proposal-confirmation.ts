export type ProposalConfirmationAction = 'confirm' | 'reject'
export type ProposalConfirmationChoice = 'accept' | 'cancel' | 'inline_required'
export type ProposalConfirmationChannel = 'native' | 'taro' | 'inline'

export interface ProposalConfirmationOptions {
  title: string
  content: string
  confirmText: string
  confirmColor: string
  showCancel: boolean
}

export interface ProposalConfirmationResult {
  confirm: boolean
  cancel: boolean
  errMsg?: string
}

export interface NativeProposalConfirmationOptions
  extends ProposalConfirmationOptions {
  success: (result: ProposalConfirmationResult) => void
  fail: (error: unknown) => void
}

export interface ProposalConfirmationDiagnostic {
  channel: Exclude<ProposalConfirmationChannel, 'inline'>
  category:
    | 'api_unavailable'
    | 'cancelled'
    | 'invalid_argument'
    | 'invalid_result'
    | 'invocation_failed'
    | 'timeout'
    | 'unknown'
  error_type: string
  error_code: string | null
}

export interface ProposalConfirmationOutcome {
  choice: ProposalConfirmationChoice
  channel: ProposalConfirmationChannel
  diagnostics: ProposalConfirmationDiagnostic[]
}

export interface ProposalConfirmationAdapters {
  native?: (options: NativeProposalConfirmationOptions) => void
  taro?: (
    options: ProposalConfirmationOptions
  ) => Promise<ProposalConfirmationResult>
}

const SAFE_ERROR_CODE = /^[a-z0-9_.:-]{1,64}$/u

export function proposalConfirmationOptions (
  action: ProposalConfirmationAction,
  changeCount: number
): ProposalConfirmationOptions {
  return {
    title: action === 'confirm' ? '确认应用这份调整？' : '确认拒绝这份提案？',
    content: action === 'confirm'
      ? `将按页面中展示的 ${changeCount} 项变化原子更新当前训练计划。`
      : '拒绝后不会修改当前训练计划，且这份提案不能再确认。',
    confirmText: action === 'confirm' ? '确认应用' : '确认拒绝',
    confirmColor: action === 'confirm' ? '#1d6b49' : '#9d382b',
    showCancel: true
  }
}

function errorText (error: unknown): string {
  if (error instanceof Error) return error.message
  if (!error || typeof error !== 'object') return String(error || '')
  const value = error as { errMsg?: unknown, message?: unknown }
  if (typeof value.errMsg === 'string') return value.errMsg
  return typeof value.message === 'string' ? value.message : ''
}

function errorCode (error: unknown): string | null {
  if (!error || typeof error !== 'object') return null
  const value = error as { errCode?: unknown, code?: unknown }
  const candidate = typeof value.errCode === 'string'
    ? value.errCode
    : typeof value.code === 'string'
      ? value.code
      : null
  return candidate && SAFE_ERROR_CODE.test(candidate) ? candidate : null
}

function errorType (error: unknown): string {
  if (error && typeof error === 'object') {
    const name = (error as { name?: unknown }).name
    if (
      typeof name === 'string' &&
      ['Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'EvalError', 'URIError'].includes(name)
    ) {
      return name
    }
  }
  if (error === null) return 'null'
  return typeof error
}

function diagnosticCategory (
  error: unknown
): ProposalConfirmationDiagnostic['category'] {
  const code = errorCode(error)
  if (code === 'api_unavailable' || code === 'invalid_result') return code
  const text = errorText(error).toLowerCase()
  if (/cancel/u.test(text)) return 'cancelled'
  if (/timeout|timed out/u.test(text)) return 'timeout'
  if (/invalid|argument|parameter/u.test(text)) return 'invalid_argument'
  if (/undefined|not a function|unavailable|not supported/u.test(text)) {
    return 'api_unavailable'
  }
  if (/fail|invoke|internal/u.test(text)) return 'invocation_failed'
  return 'unknown'
}

function sanitizedDiagnostic (
  channel: Exclude<ProposalConfirmationChannel, 'inline'>,
  error: unknown
): ProposalConfirmationDiagnostic {
  return {
    channel,
    category: diagnosticCategory(error),
    error_type: errorType(error),
    error_code: errorCode(error)
  }
}

function choiceFromResult (result: unknown): 'accept' | 'cancel' {
  if (!result || typeof result !== 'object') {
    throw { code: 'invalid_result' }
  }
  const value = result as { confirm?: unknown, cancel?: unknown }
  if (value.confirm === true) return 'accept'
  if (value.cancel === true) return 'cancel'
  throw { code: 'invalid_result' }
}

function invokeNative (
  adapter: NonNullable<ProposalConfirmationAdapters['native']>,
  options: ProposalConfirmationOptions
): Promise<ProposalConfirmationResult> {
  return new Promise((resolve, reject) => {
    try {
      adapter({
        ...options,
        success: resolve,
        fail: reject
      })
    } catch (error) {
      reject(error)
    }
  })
}

export async function requestProposalConfirmation (
  options: ProposalConfirmationOptions,
  adapters: ProposalConfirmationAdapters
): Promise<ProposalConfirmationOutcome> {
  const diagnostics: ProposalConfirmationDiagnostic[] = []

  if (adapters.native) {
    try {
      return {
        choice: choiceFromResult(await invokeNative(adapters.native, options)),
        channel: 'native',
        diagnostics
      }
    } catch (error) {
      const diagnostic = sanitizedDiagnostic('native', error)
      if (diagnostic.category === 'cancelled') {
        return { choice: 'cancel', channel: 'native', diagnostics: [diagnostic] }
      }
      diagnostics.push(diagnostic)
    }
  }

  if (adapters.taro) {
    try {
      return {
        choice: choiceFromResult(await adapters.taro(options)),
        channel: 'taro',
        diagnostics
      }
    } catch (error) {
      const diagnostic = sanitizedDiagnostic('taro', error)
      if (diagnostic.category === 'cancelled') {
        return { choice: 'cancel', channel: 'taro', diagnostics: [
          ...diagnostics,
          diagnostic
        ] }
      }
      diagnostics.push(diagnostic)
    }
  }

  return {
    choice: 'inline_required',
    channel: 'inline',
    diagnostics
  }
}
