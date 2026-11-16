import { useEffect, useRef, useState } from 'react'
import { createMotionScope, type MotionSnapshot } from './interaction-motion'

export function useInteractionMotion () {
  const [state, setState] = useState<MotionSnapshot>({ visible: true, enabled: true, active: [] })
  const scopeRef = useRef<ReturnType<typeof createMotionScope> | null>(null)
  if (!scopeRef.current) scopeRef.current = createMotionScope(setState)
  const scope = scopeRef.current
  useEffect(() => {
    scope.activate()
    return () => scope.dispose()
  }, [scope])
  return {
    ...scope, ...state,
    className: state.enabled ? 'micro-motion-enabled' : 'micro-motion-static',
    show: () => scope.setVisible(true),
    hide: () => scope.setVisible(false),
    has: (key: string) => state.enabled && state.active.includes(key)
  }
}
