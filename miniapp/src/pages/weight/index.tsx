import { useState } from 'react'
import { Button, Input, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { profileApi } from '../../services/profile'
import type { UserProfile, WeightLog } from '../../types/api'
import './index.scss'

export default function WeightPage () {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [history, setHistory] = useState<WeightLog[]>([])
  const [weight, setWeight] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const [nextProfile, nextHistory] = await Promise.all([
        profileApi.get(), profileApi.weightHistory()
      ])
      setProfile(nextProfile)
      setHistory([...nextHistory].sort((a, b) => b.recorded_at.localeCompare(a.recorded_at)))
      setWeight(nextProfile.weight_kg?.toString() || '')
    } catch (requestError) {
      setError(errorMessage(requestError, '体重记录加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useDidShow(() => { void load() })

  const save = async () => {
    const parsed = Number(weight)
    if (!Number.isFinite(parsed) || parsed < 25 || parsed > 350) {
      setError('请输入 25–350 kg 之间的有效体重')
      return
    }
    setSaving(true)
    setError('')
    try {
      await profileApi.logWeight(parsed)
      await Taro.showToast({ title: '体重已记录', icon: 'success' })
      await load()
    } catch (requestError) {
      setError(errorMessage(requestError, '体重记录失败'))
    } finally {
      setSaving(false)
    }
  }

  if (loading && !profile) return <View className='loading-state'>正在加载体重记录…</View>

  return (
    <View className='page weight-page'>
      <Text className='weight-title'>体重记录</Text>
      <Text className='weight-subtitle'>每次记录都会同步更新档案中的当前体重和 BMI。</Text>
      {error && <View className='error-banner'>{error}</View>}

      <View className='card weight-entry'>
        <View>
          <Text className='entry-label'>当前 BMI</Text>
          <Text className='entry-bmi'>{profile?.bmi == null ? '—' : profile.bmi.toFixed(1)}</Text>
          <Text className='entry-category'>{profile?.bmi_category || '记录身高和体重后计算'}</Text>
        </View>
        <View className='weight-input-wrap'>
          <Input className='weight-input' type='digit' value={weight} onInput={event => setWeight(event.detail.value)} />
          <Text className='weight-unit'>kg</Text>
        </View>
        <Button className='primary-button save-weight' loading={saving} disabled={saving} onClick={save}>记录当前体重</Button>
      </View>

      <Text className='history-title'>历史记录</Text>
      {!history.length && <View className='card empty-state'>还没有体重记录</View>}
      {history.map(item => (
        <View className='weight-row' key={item.id}>
          <Text className='weight-date'>{formatTime(item.recorded_at)}</Text>
          <Text className='weight-value'>{item.weight_kg} kg</Text>
        </View>
      ))}
    </View>
  )
}

function formatTime (value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}
