// Presentation only. This scope must never submit requests or measure workouts.
export const motionTiming = { press: 100, state: 180, enter: 240, emphasis: 600 } as const
export interface MotionSnapshot {
  visible: boolean
  enabled: boolean
  active: string[]
}

export function createMotionScope (notify: (snapshot: MotionSnapshot) => void) {
  let visible = true, disposed = false, generation = 0
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const snapshot = (): MotionSnapshot => ({ visible, enabled: visible && !disposed, active: [...timers.keys()] })
  const emit = () => { if (!disposed) notify(snapshot()) }
  const clear = () => {
    generation++
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
  }
  return {
    snapshot,
    capture: () => generation,
    isVisible: () => visible && !disposed,
    activate () { disposed = false },
    setVisible (next: boolean) {
      if (visible === next) return
      clear(); visible = next; emit()
    },
    pulse (key: string, token: number, duration: number = motionTiming.emphasis): boolean {
      if (token !== generation || !snapshot().enabled) return false
      const previous = timers.get(key)
      if (previous != null) clearTimeout(previous)
      const timer = setTimeout(() => {
        if (timers.get(key) !== timer) return
        timers.delete(key); emit()
      }, duration)
      timers.set(key, timer); emit()
      return true
    },
    cancel (key: string) {
      const timer = timers.get(key)
      if (timer == null) return
      clearTimeout(timer); timers.delete(key); emit()
    },
    dispose () { clear(); disposed = true }
  }
}
