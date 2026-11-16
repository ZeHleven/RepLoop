// Presentation only: one local layout read on open, CSS handles every frame.
export interface DisclosureSnapshot {
  expanded: boolean
  height: number | 'auto'
  opacity: number
  duration: number
}

export function createWorkoutDisclosure (
  measure: () => Promise<number | null>,
  notify: (state: DisclosureSnapshot) => void
) {
  let state: DisclosureSnapshot = { expanded: false, height: 0, opacity: 0, duration: 350 }
  let visible = true, disposed = false, generation = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const snapshot = () => ({ ...state })
  const emit = () => { if (!disposed) notify(snapshot()) }
  const cancel = () => { generation++; if (timer !== undefined) clearTimeout(timer); timer = undefined }
  const readHeight = (animate: boolean) => {
    cancel()
    const ticket = generation
    let finished = false
    const finish = (height: number | null) => {
      if (finished || disposed || !visible || !state.expanded || ticket !== generation) return
      finished = true
      if (timer !== undefined) clearTimeout(timer)
      timer = undefined
      const valid = typeof height === 'number' && Number.isFinite(height) && height > 0
      state = { expanded: true, height: valid ? height : 'auto', opacity: 1, duration: valid && animate ? 350 : 0 }
      emit()
    }
    // A failed/missing host callback must never make records inaccessible.
    timer = setTimeout(() => finish(null), 250)
    try { measure().then(finish, () => finish(null)) } catch { finish(null) }
  }
  return {
    snapshot,
    activate () { disposed = false },
    toggle () {
      if (!visible || disposed) return
      cancel()
      if (state.expanded) {
        state = { expanded: false, height: 0, opacity: 0, duration: state.height === 'auto' ? 0 : 350 }
        emit()
      } else {
        state = { ...state, expanded: true }
        emit(); readHeight(true)
      }
    },
    refresh () { if (visible && !disposed && state.expanded) readHeight(false) },
    setVisible (next: boolean) {
      if (visible === next || disposed) return
      cancel(); visible = next
      state = { ...state, height: state.expanded ? 'auto' : 0, opacity: state.expanded ? 1 : 0, duration: 0 }
      emit()
      if (visible && state.expanded) readHeight(false)
    },
    dispose () { cancel(); disposed = true }
  }
}
