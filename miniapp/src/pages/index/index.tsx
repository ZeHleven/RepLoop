import { View, Text } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { isAuthenticated } from '../../core/storage'
import { routeForProfile } from '../../services/auth'
import { profileApi } from '../../services/profile'
import './index.scss'
import { DEMO_MODE, demoApi } from '../../services/demo'

export default function Index () {
  useLoad(() => {
    const redirect = async () => {
      if (!isAuthenticated()) {
        await Taro.reLaunch({ url: '/pages/login/index' })
        return
      }
      try {
        if (DEMO_MODE) {
          await demoApi.current()
          await Taro.reLaunch({ url: '/pages/agent/index' })
          return
        }
        const profile = await profileApi.get()
        await Taro.reLaunch({ url: routeForProfile(profile.onboarding_completed) })
      } catch (_) {
        await Taro.reLaunch({ url: '/pages/login/index' })
      }
    }
    setTimeout(() => void redirect(), 80)
  })

  return (
    <View className='index'>
      <View className='brand-mark'>练</View>
      <Text className='brand-name'>训练搭子</Text>
      <Text className='brand-subtitle'>每一组，都算数</Text>
    </View>
  )
}
