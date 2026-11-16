import { useEffect, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { demoApi, DemoSession } from '../services/demo'
import { errorMessage } from '../core/request'
import './demo.scss'

export default function DemoGuide ({ busy, onPrompt }: { busy: boolean; onPrompt: (prompt: string) => void }) {
  const [session, setSession] = useState<DemoSession | null>(null)
  const [resetting, setResetting] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    demoApi.current().then(value => { if (active) setSession(value) }).catch(failure => {
      if (active) setError(errorMessage(failure, '体验信息暂时无法读取'))
    })
    return () => { active = false }
  }, [])
  async function reset () {
    if (resetting || busy) return
    const result = await Taro.showModal({ title: '重新开始体验', content: '将恢复初始示例资料，并清空当前体验中的对话。已使用的体验额度不会恢复。', confirmText: '重新开始' })
    if (!result.confirm) return
    setResetting(true)
    setError('')
    try { await demoApi.reset(); await Taro.reLaunch({ url: '/pages/agent/index' }) }
    catch (failure) { setError(errorMessage(failure, '暂时无法重新开始，请重试')) }
    finally { setResetting(false) }
  }
  return (
    <View className='demo-guide'>
      <View className='demo-guide-heading'><Text className='demo-guide-title'>小林的四周训练</Text><Text className='demo-pill'>示例体验</Text></View>
      <Text className='demo-small'>资料为虚构示例。你可以改写问题，观察 AI 如何使用训练记录回答。</Text>
      {session && !session.model_available && <Text className='demo-offline'>实时 AI 暂未接通，可以先查看示例计划和训练历史。</Text>}
      <View className='demo-actions'>
        <Button onClick={() => Taro.switchTab({ url: '/pages/workouts/index' })}>查看计划</Button>
        <Button onClick={() => Taro.navigateTo({ url: '/pages/history/index' })}>训练历史</Button>
        <Button disabled={busy || resetting || !session?.resets_remaining} onClick={reset}>{resetting ? '正在重置…' : '重新体验'}</Button>
      </View>
      <View className='demo-prompts'>{session?.quick_prompts.map((prompt, index) => <Button disabled={busy || resetting || !session.model_available} key={prompt} onClick={() => onPrompt(prompt)}><Text className='demo-prompt-number'>{index + 1}</Text>{prompt}</Button>)}</View>
      {error && <View className='demo-offline' role='alert'>{error}</View>}
    </View>
  )
}
