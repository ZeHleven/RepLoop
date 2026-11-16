import { useRef, useState } from 'react'
import { Button, Input, Picker, Slider, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { profileApi } from '../../services/profile'
import type { UserProfile } from '../../types/api'
import './index.scss'

const genders = [
  { value: 'male', label: '男' },
  { value: 'female', label: '女' },
  { value: 'prefer_not_to_say', label: '不愿透露' }
]
const goals = [
  { value: 'fat_loss', label: '减脂' },
  { value: 'muscle_gain', label: '增肌' },
  { value: 'strength', label: '力量提升' },
  { value: 'endurance', label: '耐力提升' },
  { value: 'flexibility', label: '灵活性改善' },
  { value: 'general_fitness', label: '综合体能' }
]
const experiences = [
  { value: 'beginner', label: '新手' },
  { value: 'intermediate', label: '进阶训练者' },
  { value: 'advanced', label: '熟练训练者' }
]
const locations = [
  { value: 'gym', label: '健身房' },
  { value: 'home', label: '居家' },
  { value: 'outdoor', label: '户外' }
]

interface Draft {
  age: string
  height: string
  gender: string
  goal: string
  experience: string
  location: string
  days: number
  duration: number
}

export default function ProfileEditPage () {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveNotice, setSaveNotice] = useState('')
  const [error, setError] = useState('')
  const savingRef = useRef(false)
  const savedRef = useRef(false)
  const returningRef = useRef(false)

  useLoad(() => {
    void profileApi.get().then(profile => setDraft(fromProfile(profile))).catch(requestError => {
      setError(errorMessage(requestError, '个人档案加载失败'))
    })
  })

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    if (savingRef.current || returningRef.current || draft?.[key] === value) return
    savedRef.current = false
    setSaved(false)
    setSaveNotice('')
    setDraft(current => current ? { ...current, [key]: value } : current)
  }

  const returnAfterSave = async () => {
    if (!savedRef.current || returningRef.current) return
    returningRef.current = true
    try {
      await Taro.navigateBack()
    } catch {
      setSaveNotice('档案已保存，自动返回失败。请点击“返回上一页”或左上角返回，无需再次保存。')
    } finally {
      returningRef.current = false
    }
  }

  const save = async () => {
    if (!draft || savingRef.current || savedRef.current || returningRef.current) return
    const age = Number(draft.age)
    const height = Number(draft.height)
    if (!Number.isInteger(age) || age < 12 || age > 100) {
      setError('年龄需为 12–100 岁的整数')
      return
    }
    if (!Number.isFinite(height) || height < 100 || height > 250) {
      setError('身高需在 100–250 cm 之间')
      return
    }
    if (!draft.gender || !draft.goal || !draft.experience || !draft.location) {
      setError('请完整选择性别、训练目标、经验和训练地点')
      return
    }
    savingRef.current = true
    setSaving(true)
    setError('')
    setSaveNotice('')
    try {
      try {
        await profileApi.update({
          age,
          height_cm: height,
          gender: draft.gender,
          primary_goal: draft.goal,
          experience_level: draft.experience,
          training_location: draft.location,
          training_days_per_week: draft.days,
          session_duration_min: draft.duration
        })
      } catch (requestError) {
        setError(errorMessage(requestError, '个人档案保存失败'))
        return
      }
      savedRef.current = true
      setSaved(true)
      setSaveNotice('档案已保存。')
      try {
        await Taro.showToast({ title: '档案已保存', icon: 'success' })
      } catch {
        // Native toast failures must not turn a confirmed write into a save error.
      }
      await returnAfterSave()
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  if (!draft) return <View className='loading-state'>正在加载个人档案…</View>

  return (
    <View className='page profile-edit-page'>
      <Text className='edit-title'>个人档案</Text>
      <Text className='edit-subtitle'>训练偏好用于生成建议，不会自动修改当前训练计划。</Text>
      {error && <View className='error-banner'>{error}</View>}
      {saveNotice && <View className='save-notice'>{saveNotice}</View>}
      <View className='card edit-card'>
        <Field label='年龄'>
          <Input className='edit-input' type='number' disabled={saving} value={draft.age} onInput={event => update('age', event.detail.value)} />
        </Field>
        <Field label='身高（cm）'>
          <Input className='edit-input' type='digit' disabled={saving} value={draft.height} onInput={event => update('height', event.detail.value)} />
        </Field>
        <Selector label='性别' options={genders} disabled={saving} value={draft.gender} onChange={value => update('gender', value)} />
        <Selector label='主要目标' options={goals} disabled={saving} value={draft.goal} onChange={value => update('goal', value)} />
        <Selector label='训练经验' options={experiences} disabled={saving} value={draft.experience} onChange={value => update('experience', value)} />
        <Selector label='训练地点' options={locations} disabled={saving} value={draft.location} onChange={value => update('location', value)} />
        <Text className='field-label'>每周训练偏好：{draft.days} 天</Text>
        <Slider min={1} max={7} step={1} disabled={saving} value={draft.days} activeColor='#1d6b49' onChange={event => update('days', event.detail.value)} />
        <Text className='field-label'>单次训练时长：{draft.duration} 分钟</Text>
        <Slider min={20} max={120} step={5} disabled={saving} value={draft.duration} activeColor='#1d6b49' onChange={event => update('duration', event.detail.value)} />
      </View>
      <Button className='primary-button' loading={saving} disabled={saving || saved} onClick={save}>{saved ? '已保存' : '保存档案'}</Button>
      {saved && !saving && <Button className='secondary-button return-after-save' onClick={returnAfterSave}>返回上一页</Button>}
    </View>
  )
}

function Field ({ label, children }: { label: string, children: React.ReactNode }) {
  return <View className='edit-field'><Text className='field-label'>{label}</Text>{children}</View>
}

function Selector ({
  label,
  options,
  disabled,
  value,
  onChange
}: {
  label: string
  options: Array<{ value: string, label: string }>
  disabled: boolean
  value: string
  onChange: (value: string) => void
}) {
  const index = Math.max(0, options.findIndex(item => item.value === value))
  return (
    <View className='edit-field'>
      <Text className='field-label'>{label}</Text>
      <Picker mode='selector' disabled={disabled} range={options.map(item => item.label)} value={index} onChange={event => onChange(options[Number(event.detail.value)].value)}>
        <View className='picker-value'>{options[index].label}<Text>⌄</Text></View>
      </Picker>
    </View>
  )
}

function fromProfile (profile: UserProfile): Draft {
  return {
    age: profile.age?.toString() || '',
    height: profile.height_cm?.toString() || '',
    gender: profile.gender || 'prefer_not_to_say',
    goal: profile.primary_goal || 'general_fitness',
    experience: profile.experience_level || 'beginner',
    location: profile.training_location || 'gym',
    days: profile.training_days_per_week || 3,
    duration: profile.session_duration_min || 45
  }
}
