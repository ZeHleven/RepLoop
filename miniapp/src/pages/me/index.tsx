import { useEffect, useRef, useState } from 'react'
import { capsuleNavigation } from '../../core/capsule-platform'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { peekCached, readCached } from '../../core/read-cache'
import LoadingFeedback from '../../components/LoadingFeedback'

import { errorMessage } from '../../core/request'
import { clearTokens } from '../../core/storage'
import { profileApi } from '../../services/profile'
import type { UserProfile, WeightLog } from '../../types/api'
import './index.scss'

export default function MePage () {
  // A profile may have been cached by the training tab before weights were read.
  // Only restore a complete summary; missing weight history must not look like zero.
  const [profile, setProfile] = useState<UserProfile | null>(() =>
    peekCached<WeightLog[]>('weights') === undefined ? null : peekCached<UserProfile>('profile') || null)
  const [weights, setWeights] = useState<WeightLog[]>(() => peekCached<WeightLog[]>('weights') || [])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [visible, setVisible] = useState(true)
  const generation = useRef(0)

  const load = async (force = true) => {
    const ticket = ++generation.current
    setLoading(true)
    setError('')
    try {
      const [nextProfile, nextWeights] = await Promise.all([
        readCached('profile', profileApi.get, force),
        readCached('weights', profileApi.weightHistory, force)
      ])
      if (ticket !== generation.current) return
      setProfile(nextProfile)
      setWeights(nextWeights)
    } catch (requestError) {
      if (ticket === generation.current) setError(errorMessage(requestError, '个人资料加载失败'))
    } finally {
      if (ticket === generation.current) setLoading(false)
    }
  }

  useDidShow(() => { capsuleNavigation.show(3); setVisible(true); void load(false) })
  useDidHide(() => { setVisible(false); generation.current++ })
  useEffect(() => () => { generation.current++ }, [])

  const logout = async () => {
    const result = await Taro.showModal({
      title: '退出登录？',
      content: '本机保存的登录状态将被清除。'
    })
    if (!result.confirm) return
    clearTokens()
    await Taro.reLaunch({ url: '/pages/login/index' })
  }

  return (
    <View className='page me-page'>
      <Text className='me-eyebrow'>训练资料中心</Text>
      <Text className='me-title'>我的</Text>
      {error && <View className='error-banner'>{error}<Button className='secondary-button me-retry' onClick={() => load()}>重新加载</Button></View>}
      <LoadingFeedback loading={loading} hasContent={Boolean(profile)} visible={visible} text='正在加载个人资料…' refreshingText='正在更新个人资料…' />
      {profile && (
        <>
          <View className='card profile-summary'>
            <View className='profile-mark'>我</View>
            <View className='profile-copy'>
              <Text className='profile-primary'>{goalLabel(profile.primary_goal)}</Text>
              <Text className='profile-secondary'>
                {profile.experience_level ? experienceLabel(profile.experience_level) : '训练经验待完善'}
                {' · '}{profile.training_location ? locationLabel(profile.training_location) : '地点待完善'}
              </Text>
            </View>
          </View>

          <View className='metric-row'>
            <Metric value={profile.weight_kg == null ? '—' : `${profile.weight_kg} kg`} label='当前体重' />
            <Metric value={profile.bmi == null ? '—' : profile.bmi.toFixed(1)} label='BMI' />
            <Metric value={weights.length} label='体重记录' />
          </View>

          <View className='card me-menu'>
            <MenuItem
              title='个人档案'
              subtitle='基础资料、目标和训练偏好'
              onClick={() => Taro.navigateTo({ url: '/pages/profile-edit/index' })}
            />
            <MenuItem
              title='健康与饮食偏好'
              subtitle={`${profile.injuries.length} 项伤病 · ${profile.chronic_conditions.length} 项慢性情况`}
              onClick={() => Taro.navigateTo({ url: '/pages/health-edit/index' })}
            />
            <MenuItem
              title='体重记录'
              subtitle='记录当前体重并查看变化'
              onClick={() => Taro.navigateTo({ url: '/pages/weight/index' })}
              last
            />
          </View>
          <Button className='danger-button logout-button' onClick={logout}>退出登录</Button>
        </>
      )}
    </View>
  )
}

function Metric ({ value, label }: { value: string | number, label: string }) {
  return (
    <View className='me-metric'>
      <Text className='me-metric-value'>{value}</Text>
      <Text className='me-metric-label'>{label}</Text>
    </View>
  )
}

function MenuItem ({
  title,
  subtitle,
  onClick,
  last = false
}: {
  title: string
  subtitle: string
  onClick: () => void
  last?: boolean
}) {
  return (
    <View className={`menu-item ${last ? 'last' : ''}`} onClick={onClick}>
      <View>
        <Text className='menu-title'>{title}</Text>
        <Text className='menu-subtitle'>{subtitle}</Text>
      </View>
      <Text className='menu-arrow'>›</Text>
    </View>
  )
}

function goalLabel (value?: string | null): string {
  return ({
    fat_loss: '减脂', muscle_gain: '增肌', strength: '力量提升',
    endurance: '耐力提升', flexibility: '灵活性改善', general_fitness: '综合体能'
  } as Record<string, string>)[value || ''] || '训练目标待完善'
}

function experienceLabel (value: string): string {
  return ({ beginner: '新手', intermediate: '进阶', advanced: '熟练' } as Record<string, string>)[value] || value
}

function locationLabel (value: string): string {
  return ({ gym: '健身房', home: '居家', outdoor: '户外' } as Record<string, string>)[value] || value
}
