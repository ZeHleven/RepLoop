import { Image, Text, View } from '@tarojs/components'
import type { CSSProperties } from 'react'
import { capsuleTabs } from '../core/capsule-navigation'
import { capsuleNavigation, useCapsuleState } from '../core/capsule-platform'
import training from '../assets/navigation/training.svg'
import trainingSelected from '../assets/navigation/training-selected.svg'
import nutrition from '../assets/navigation/nutrition.svg'
import nutritionSelected from '../assets/navigation/nutrition-selected.svg'
import agent from '../assets/navigation/agent.svg'
import agentSelected from '../assets/navigation/agent-selected.svg'
import me from '../assets/navigation/me.svg'
import meSelected from '../assets/navigation/me-selected.svg'
import './index.scss'
const icons=[[training,trainingSelected],[nutrition,nutritionSelected],[agent,agentSelected],[me,meSelected]]
export default function CapsuleTabBar () {
  const state=useCapsuleState()
  const elapsed=Math.max(0,Date.now()-state.started)
  const moving=state.animate&&elapsed<300
  return <View className={`capsule-host ${state.keyboard?'capsule-hidden':''}`}>
    {state.error&&<Text className='capsule-error'>{state.error}</Text>}
    <View className='capsule-bar'>
      <View key={`pill-${state.revision}`} className={`capsule-pill ${moving?'capsule-moving':''}`} style={{'--capsule-from':`${state.from*100}%`,'--capsule-to':`${state.selected*100}%`,transform:`translateX(${state.selected*100}%)`,animationDelay:`-${Math.min(elapsed,300)}ms`} as CSSProperties} />
      {capsuleTabs.map((tab,index)=><View key={tab.path} className={`capsule-item ${state.selected===index?'capsule-selected':''}`} ariaRole='button' ariaLabel={`${tab.label}${state.selected===index?'，已选中':''}`} onClick={()=>capsuleNavigation.select(index)}>
        <View key={`icon-${state.revision}-${index}`} className={`capsule-icon capsule-kind-${index} ${state.animate&&elapsed<320&&state.selected===index?'capsule-icon-playing':''}`} style={{animationDelay:`-${Math.min(elapsed,320)}ms`}}>
          <Image src={icons[index][0]} className='capsule-image' mode='scaleToFill' style={{opacity:state.selected===index?0:1}} />
          <Image src={icons[index][1]} className='capsule-image' mode='scaleToFill' style={{opacity:state.selected===index?1:0}} />
        </View><Text className='capsule-label'>{tab.label}</Text>
      </View>)}
    </View>
  </View>
}

// Webpack extracts this stylesheet into app.wxss; allow it across the native
// custom-tab-bar component boundary. Other app components keep their defaults.
CapsuleTabBar.options = { addGlobalClass: true }
