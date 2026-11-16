import { NavigationBar, PageMeta } from '@tarojs/components'
import { navigationTheme } from '../core/navigation-theme'

export default function PlanPageMeta ({ title, scrollLocked = false }: { title: string, scrollLocked?: boolean }) {
  // enablePageMeta emits a native navigation-bar even without JSX children.
  // Set its colors explicitly, including the initial loading/error render.
  return <PageMeta className='plan-sheet-meta' pageStyle={scrollLocked ? 'overflow: hidden;' : ''}>
    <NavigationBar className='plan-navigation-bar' title={title}
      backgroundColor={navigationTheme.backgroundColor} frontColor={navigationTheme.frontColor}
      colorAnimationDuration='0' />
  </PageMeta>
}
