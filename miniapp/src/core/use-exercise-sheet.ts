import { useEffect, useRef, useState } from 'react'
import Taro, { useDidHide } from '@tarojs/taro'

type Phase = 'closed' | 'opening' | 'open' | 'closing'
const EXIT_MS = 240

// Only the small sheet/overlay transition is animated. Never scroll the page
// or run a per-frame JS animation over the long form.
export function useExerciseSheet (onOpenChange?: (open: boolean) => void) {
  const [phase, setPhase] = useState<Phase>('closed')
  const [created, setCreated] = useState(false)
  const [keyboardHeight, setKeyboardHeight] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(0)
  const [scrollTarget, setScrollTarget] = useState('')
  const current = useRef<Phase>('closed')
  const mounted = useRef(true)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const revision = useRef(0)
  const focused = useRef('')
  const notified = useRef(false)
  const change = useRef(onOpenChange)
  change.current = onOpenChange
  const clearTimer = () => { if (timer.current !== null) clearTimeout(timer.current); timer.current = null }
  const notify = (value: boolean) => {
    if (notified.current === value) return
    notified.current = value; change.current?.(value)
  }
  const move = (value: Phase) => { current.current = value; if (mounted.current) setPhase(value) }
  const revealFocus = () => {
    if (!focused.current || current.current !== 'open') return
    const target = focused.current, generation = revision.current
    setScrollTarget('')
    Taro.nextTick(() => {
      if (mounted.current && revision.current === generation && current.current === 'open') setScrollTarget(target)
    })
  }
  const dismiss = (immediately = false) => {
    if (current.current === 'closed' || (current.current === 'closing' && !immediately)) return
    revision.current++; clearTimer(); focused.current = ''
    setKeyboardHeight(0); setScrollTarget('')
    // This is presentation cleanup only; it never cancels/replays a request.
    void Taro.hideKeyboard?.().catch(() => {})
    if (immediately) { move('closed'); notify(false); return }
    move('closing')
    const generation = revision.current
    timer.current = setTimeout(() => {
      timer.current = null
      if (!mounted.current || revision.current !== generation) return
      move('closed'); notify(false)
    }, EXIT_MS)
  }
  const open = () => {
    if (!mounted.current || current.current === 'open' || current.current === 'opening') return false
    const reversing = current.current === 'closing'
    revision.current++; clearTimer(); setCreated(true); setKeyboardHeight(0)
    try { const height = Taro.getWindowInfo?.().windowHeight; if (height && Number.isFinite(height)) setViewportHeight(height) } catch (_) { /* CSS viewport fallback */ }
    notify(true)
    if (reversing) move('open')
    else {
      move('opening')
      const generation = revision.current
      timer.current = setTimeout(() => {
        timer.current = null
        if (mounted.current && revision.current === generation) move('open')
      }, 20)
    }
    return true
  }
  const listening = phase === 'open' || phase === 'opening'
  useEffect(() => {
    if (!listening) return
    const onKeyboard = ({ height }: { height: number }) => {
      if (!mounted.current || !['opening', 'open'].includes(current.current)) return
      setKeyboardHeight(Number.isFinite(height) ? Math.max(0, height) : 0)
      revealFocus()
    }
    Taro.onKeyboardHeightChange?.(onKeyboard)
    return () => { Taro.offKeyboardHeightChange?.(onKeyboard) }
  }, [listening])
  useDidHide(() => dismiss(true))
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; revision.current++; clearTimer(); current.current = 'closed'; notify(false) }
  }, [])
  return {
    phase, created, keyboardHeight, scrollTarget,
    isOpen: () => current.current === 'open' || current.current === 'opening',
    isMounted: () => mounted.current,
    open, dismiss,
    focus: (id: string) => { focused.current = id; revealFocus() },
    panelStyle: {
      bottom: `${keyboardHeight}px`,
      height: viewportHeight ? `${Math.min(viewportHeight * 0.88, Math.max(0, viewportHeight - keyboardHeight - 16))}px` : `calc(88vh - ${keyboardHeight}px)`
    }
  }
}
