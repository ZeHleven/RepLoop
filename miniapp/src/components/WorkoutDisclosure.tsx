import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Text, View, type ITouchEvent } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { createWorkoutDisclosure, type DisclosureSnapshot } from '../core/workout-disclosure'
import './workout-disclosure.scss'

let nextId = 0
interface Props {
  title: string
  summary: string
  className?: string
  visible: boolean
  layoutVersion: number
  contentKey: unknown
  children: ReactNode
  headerOnly?: boolean
}

export default function WorkoutDisclosure ({ title, summary, className = '', visible, layoutVersion, contentKey, children, headerOnly = false }: Props) {
  const [id] = useState(() => `workout-disclosure-${++nextId}`)
  const [state, setState] = useState<DisclosureSnapshot>({ expanded: false, height: 0, opacity: 0, duration: 350 })
  const controller = useRef<ReturnType<typeof createWorkoutDisclosure> | null>(null)
  const origin = useRef<{ x: number, y: number } | null>(null)
  const dragged = useRef(false)
  if (!controller.current) {
    const page = Taro.getCurrentInstance?.()?.page
    controller.current = createWorkoutDisclosure(() => new Promise(resolve => {
      const read = () => {
        try {
          let query = Taro.createSelectorQuery()
          if (page) query = query.in(page)
          query.select(`#${id}-inner`).boundingClientRect(rect => {
            resolve(rect && !Array.isArray(rect) ? rect.height : null)
          }).exec()
        } catch { resolve(null) }
      }
      if (Taro.nextTick) Taro.nextTick(read); else read()
    }), setState)
  }
  const scope = controller.current
  useEffect(() => { scope.activate(); return () => scope.dispose() }, [scope])
  useEffect(() => { scope.setVisible(visible) }, [scope, visible])
  useEffect(() => { scope.refresh() }, [scope, contentKey, layoutVersion])
  const touchStart = (event: ITouchEvent) => {
    const touch = event.touches?.[0]
    origin.current = touch ? { x: touch.clientX, y: touch.clientY } : null
    dragged.current = false
  }
  const touchMove = (event: ITouchEvent) => {
    const touch = event.touches?.[0], start = origin.current
    if (touch && start && (Math.abs(touch.clientX - start.x) > 8 || Math.abs(touch.clientY - start.y) > 8)) dragged.current = true
  }
  const trigger = {
    ariaRole: 'button' as const, ariaLabel: `${title}，${summary}，${state.expanded ? '已展开' : '已收起'}`,
    onTouchStart: touchStart, onTouchMove: touchMove, onTouchCancel: () => { dragged.current = true },
    onClick: () => { if (!dragged.current) scope.toggle() }
  }
  return <View className={`workout-disclosure ${className}`} {...(headerOnly ? {} : trigger)}>
    <View className='workout-disclosure-heading' {...(headerOnly ? trigger : {})}>
      <Text className='workout-disclosure-title'>{title}</Text><Text className='workout-disclosure-summary'>{summary}</Text>
    </View>
    <View className='workout-disclosure-body' aria-hidden={!state.expanded} style={{ height: state.height === 'auto' ? 'auto' : `${state.height}px`, opacity: state.opacity, transitionDuration: `${state.duration}ms`, pointerEvents: state.expanded ? 'auto' : 'none' }}>
      <View id={`${id}-inner`} className='workout-disclosure-inner'>{children}</View>
    </View>
  </View>
}
