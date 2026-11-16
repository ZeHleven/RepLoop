import { useEffect, useRef, useState } from 'react'
import { ScrollView, View } from '@tarojs/components'
import type { ScrollViewProps } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { scrollEdgeSizes } from '../core/scroll-edges'
import './EdgeScrollView.scss'

type Props = ScrollViewProps & { contentSelector: string, visible: boolean }

export default function EdgeScrollView ({ contentSelector, visible, children, onScroll, ...props }: Props) {
  const [edges, setEdges] = useState({ top: 0, bottom: 0 })
  const geometry = useRef({ height: 0, width: 0, content: 0, top: 0 })
  const publish = () => {
    const g = geometry.current
    const next = scrollEdgeSizes(g.top, g.content, g.height, g.width)
    setEdges(prior => prior.top === next.top && prior.bottom === next.bottom ? prior : next)
  }
  useEffect(() => {
    if (!visible || typeof Taro.createSelectorQuery !== 'function') return
    let alive = true
    let queryRevision = 0
    const measure = () => {
      if (!alive || typeof Taro.createSelectorQuery !== 'function') return
      const revision = ++queryRevision
      try {
        const query = Taro.createSelectorQuery()
        query.select(`.${props.className}`).boundingClientRect()
        query.select(`.${props.className}`).scrollOffset()
        query.select(contentSelector).boundingClientRect()
        query.exec(results => {
          if (!alive || revision !== queryRevision) return
          const [viewport, offset, content] = results || []
          if (!(viewport?.height > 0) || !(viewport?.width > 0) || !(content?.height >= 0)) return
          geometry.current = { height: viewport.height, width: viewport.width, content: content.height, top: offset?.scrollTop ?? geometry.current.top }
          publish()
        })
      } catch { /* Optional visual enhancement; never interfere with records. */ }
    }
    const nextMeasure = () => { try { Taro.nextTick(measure) } catch { measure() } }
    nextMeasure()
    // One final measurement after existing disclosure/layout transitions, no polling.
    const timer = setTimeout(nextMeasure, 350)
    if (typeof Taro.onWindowResize === 'function') Taro.onWindowResize(nextMeasure)
    return () => {
      alive = false; queryRevision++; clearTimeout(timer)
      if (typeof Taro.offWindowResize === 'function') Taro.offWindowResize(nextMeasure)
    }
  }, [children, visible, props.className, contentSelector])
  const handleScroll: NonNullable<ScrollViewProps['onScroll']> = event => {
    if (visible) {
      geometry.current.top = event.detail.scrollTop
      if (Number.isFinite(event.detail.scrollHeight)) geometry.current.content = event.detail.scrollHeight
      publish()
    }
    onScroll?.(event)
  }
  return <View className='scroll-edge-frame'>
    <ScrollView {...props} onScroll={handleScroll}>{children}</ScrollView>
    <View className='scroll-edge scroll-edge-top' aria-hidden='true' style={{ height: `${visible ? edges.top : 0}px` }} />
    <View className='scroll-edge scroll-edge-bottom' aria-hidden='true' style={{ height: `${visible ? edges.bottom : 0}px` }} />
  </View>
}
