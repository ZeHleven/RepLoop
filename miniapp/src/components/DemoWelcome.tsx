import { useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { demoApi } from '../services/demo'
import { errorMessage } from '../core/request'
import { miniappBuildLabel } from '../core/build-info'
import './demo.scss'

export default function DemoWelcome () {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function start () {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await demoApi.start()
      await Taro.reLaunch({ url: '/pages/agent/index' })
    } catch (failure) { setError(errorMessage(failure, '暂时无法开始体验，请稍后重试')) }
    finally { setBusy(false) }
  }
  return (
    <View className='demo-welcome page'>
      <View className='demo-brand'><Text className='demo-brand-mark'>练</Text><Text>循练 · RepLoop</Text><Text className='demo-pill'>交互演示</Text></View>
      <View className='demo-intro'>
        <Text className='demo-eyebrow'>从每一次训练，到下一次进步</Text>
        <Text className='demo-title'>一个了解你训练记录的 AI 搭子。</Text>
        <Text className='demo-description'>带着计划与历史数据对话，看看建议如何随你的问题改变。</Text>
      </View>
      <View className='demo-persona'>
        <View className='demo-persona-heading'><Text className='demo-avatar'>林</Text><View><Text className='demo-persona-name'>从小林的训练开始</Text><Text className='demo-small'>虚构人物 · 独立体验空间</Text></View></View>
        <View className='demo-facts'><Text>28 岁</Text><Text>每周 3 练</Text><Text>4 周记录</Text></View>
        <Text className='demo-small'>已备好全身力量计划、训练组次和体重变化。进入后可以直接提问，也可以查看这些记录。</Text>
      </View>
      <View className='demo-steps'>
        <View><Text className='demo-step-number'>01</Text><Text>问问最近练得怎么样</Text></View>
        <View><Text className='demo-step-number'>02</Text><Text>接着追问，比较前后变化</Text></View>
        <View><Text className='demo-step-number'>03</Text><Text>加入新条件，获得训练建议</Text></View>
      </View>
      {error && <View className='error-banner' role='alert'>{error}</View>}
      <Button className='primary-button demo-start' disabled={busy} onClick={start}>{busy ? '正在准备你的体验…' : '立即体验'}</Button>
      <Text className='demo-footnote'>无需注册或填写个人资料 · 使用虚构示例数据</Text>
      <Text className='demo-build'>{miniappBuildLabel()}</Text>
    </View>
  )
}
