import { useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { capsuleTabs, createCapsuleNavigation, tabIndex } from './capsule-navigation'

export const capsuleNavigation = createCapsuleNavigation({
  current: () => { const pages=Taro.getCurrentPages?.() || [];return tabIndex(pages[pages.length-1]?.route || '') },
  switchTab: index => Taro.switchTab({url:capsuleTabs[index].path}),
  hideKeyboard: () => {void Taro.hideKeyboard?.().catch(()=>{})}
})
let subscribers=0
const keyboardChanged=(event: {height:number})=>capsuleNavigation.keyboard(event.height)
export function useCapsuleState () {
  const [state,setState]=useState(capsuleNavigation.snapshot)
  useEffect(()=>{
    if(subscribers++===0)Taro.onKeyboardHeightChange?.(keyboardChanged)
    const unsubscribe=capsuleNavigation.subscribe(setState)
    return ()=>{unsubscribe();if(--subscribers===0)Taro.offKeyboardHeightChange?.(keyboardChanged)}
  },[])
  return state
}
