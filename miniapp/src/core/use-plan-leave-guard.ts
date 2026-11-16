import { useEffect, useRef } from 'react'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'

// Serialize native calls: a delayed enable must never win over a later undo.
export function usePlanLeaveGuard (dirty: boolean) {
  const desired = useRef(dirty)
  const active = useRef(true)
  const generation = useRef(0)
  const queue = useRef(Promise.resolve())
  desired.current = dirty
  const sync = () => {
    const revision = ++generation.current
    queue.current = queue.current.catch(() => {}).then(async () => {
      if (!active.current || revision !== generation.current) return
      if (desired.current) await Taro.enableAlertBeforeUnload({ message: '有尚未保存的修改，确定离开计划编辑器吗？' })
      else await Taro.disableAlertBeforeUnload()
    }).catch(() => {}) // A host UI failure must not be reported as a failed business save.
    return queue.current
  }
  useEffect(() => { void sync() }, [dirty])
  useDidHide(() => { active.current = false; generation.current++ })
  useDidShow(() => { active.current = true; void sync() })
  useEffect(() => () => { active.current = false; generation.current++ }, [])
  return {
    beforeNavigation: async () => {
      desired.current = false
      await sync()
      // A baseline render after navigateTo must not disable the next page's guard.
      active.current = false; generation.current++
    },
    resume: () => { active.current = true; void sync() }
  }
}
