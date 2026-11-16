import { useState } from 'react'
import { Button, Input, Slider, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { profileApi } from '../../services/profile'
import type { UserProfile } from '../../types/api'
import './index.scss'

const TOTAL_STEPS = 6

interface OnboardingDraft {
  gender: string
  age: string
  heightCm: string
  weightKg: string
  goal: string
  experience: string
  location: string
  trainingDays: number
  sessionDuration: number
  injuries: string[]
  chronicConditions: string[]
  dietRestriction: string
  safetyAcknowledged: boolean
}

const defaultDraft: OnboardingDraft = {
  gender: '',
  age: '',
  heightCm: '',
  weightKg: '',
  goal: '',
  experience: '',
  location: '',
  trainingDays: 3,
  sessionDuration: 45,
  injuries: [],
  chronicConditions: [],
  dietRestriction: 'none',
  safetyAcknowledged: false
}

const genderOptions = [
  ['male', '男'],
  ['female', '女'],
  ['other', '其他'],
  ['prefer_not_to_say', '暂不透露']
]

const goalOptions = [
  ['fat_loss', '减脂'],
  ['muscle_gain', '增肌'],
  ['strength', '提升力量'],
  ['endurance', '增强耐力'],
  ['flexibility', '改善柔韧'],
  ['general_fitness', '综合健身']
]

const experienceOptions = [
  ['beginner', '新手 · 不足 1 年'],
  ['intermediate', '进阶 · 1–3 年'],
  ['advanced', '熟练 · 3 年以上']
]

const locationOptions = [
  ['gym', '健身房'],
  ['home', '居家'],
  ['outdoor', '户外']
]

const injuryOptions = ['none', '膝关节', '肩关节', '腰背部', '踝关节', '腕肘部', '其他']
const chronicOptions = ['none', '高血压', '糖尿病', '心脏病', '哮喘', '其他']

const dietOptions = [
  ['none', '无特殊限制'],
  ['vegetarian', '素食'],
  ['halal', '清真'],
  ['lactose_free', '不耐乳糖'],
  ['other', '其他']
]

export default function OnboardingPage () {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<OnboardingDraft>(defaultDraft)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const profile = await profileApi.get()
      if (profile.onboarding_completed) {
        await Taro.reLaunch({ url: '/pages/workouts/index' })
        return
      }
      setDraft(draftFromProfile(profile))
    } catch (requestError) {
      setError(errorMessage(requestError, '训练档案加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useLoad(() => {
    void load()
  })

  const update = <K extends keyof OnboardingDraft>(
    key: K,
    value: OnboardingDraft[K]
  ) => setDraft(current => ({ ...current, [key]: value }))

  const toggleHealth = (
    key: 'injuries' | 'chronicConditions',
    value: string
  ) => {
    setDraft(current => {
      if (value === 'none') return { ...current, [key]: ['none'] }
      const values = new Set(current[key].filter(item => item !== 'none'))
      if (values.has(value)) values.delete(value)
      else values.add(value)
      return { ...current, [key]: [...values] }
    })
  }

  const next = async () => {
    const validationError = validateStep(step, draft)
    if (validationError) {
      setError(validationError)
      return
    }
    setError('')
    if (step < TOTAL_STEPS - 1) {
      setStep(current => current + 1)
      return
    }

    setSaving(true)
    try {
      await profileApi.update({
        gender: draft.gender,
        age: Number(draft.age),
        height_cm: Number(draft.heightCm),
        weight_kg: Number(draft.weightKg),
        primary_goal: draft.goal,
        experience_level: draft.experience,
        training_location: draft.location,
        training_days_per_week: draft.trainingDays,
        session_duration_min: draft.sessionDuration,
        injuries: draft.injuries.includes('none') ? [] : draft.injuries,
        chronic_conditions: draft.chronicConditions.includes('none')
          ? []
          : draft.chronicConditions,
        diet_restriction: draft.dietRestriction,
        onboarding_completed: true
      })
      await Taro.reLaunch({ url: '/pages/plan-builder/index' })
    } catch (requestError) {
      setError(errorMessage(requestError, '资料保存失败，请重试'))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <View className='loading-state'>正在准备你的训练档案…</View>

  return (
    <View className='page onboarding-page'>
      <View className='progress-heading'>
        <Text>第 {step + 1} / {TOTAL_STEPS} 步</Text>
        <Text>{Math.round((step + 1) / TOTAL_STEPS * 100)}%</Text>
      </View>
      <View className='progress-track'>
        <View className='progress-value' style={{ width: `${(step + 1) / TOTAL_STEPS * 100}%` }} />
      </View>

      {error && <View className='error-banner onboarding-error'>{error}</View>}

      <View className='step-card card'>
        {step === 0 && <BasicStep draft={draft} update={update} />}
        {step === 1 && (
          <ChoiceStep
            title='这段训练最想实现什么？'
            subtitle='我们会据此安排动作、强度和进度节奏。'
            value={draft.goal}
            options={goalOptions}
            onChange={value => update('goal', value)}
          />
        )}
        {step === 2 && (
          <ChoiceStep
            title='你的训练经验如何？'
            subtitle='如实选择能帮助我们避免过难或过轻的计划。'
            value={draft.experience}
            options={experienceOptions}
            onChange={value => update('experience', value)}
            vertical
          />
        )}
        {step === 3 && <PreferenceStep draft={draft} update={update} />}
        {step === 4 && (
          <HealthStep
            draft={draft}
            onToggle={toggleHealth}
          />
        )}
        {step === 5 && <ConfirmStep draft={draft} update={update} />}
      </View>

      <View className='onboarding-actions'>
        {step > 0 && (
          <Button
            className='secondary-button back-button'
            disabled={saving}
            onClick={() => { setError(''); setStep(current => current - 1) }}
          >
            上一步
          </Button>
        )}
        <Button
          className='primary-button next-button'
          disabled={saving}
          onClick={next}
        >
          {saving ? '正在保存…' : step === TOTAL_STEPS - 1 ? '完成并开始训练' : '下一步'}
        </Button>
      </View>
    </View>
  )
}

function BasicStep ({
  draft,
  update
}: {
  draft: OnboardingDraft
  update: <K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) => void
}) {
  return (
    <View>
      <StepTitle title='先认识一下你' subtitle='这些数据用于估算训练负荷与身体指标。' />
      <Text className='field-title'>性别</Text>
      <OptionGrid
        options={genderOptions}
        value={draft.gender}
        onChange={value => update('gender', value)}
      />
      <View className='measurement-grid'>
        <MeasurementInput label='年龄' unit='岁' value={draft.age} onChange={value => update('age', value)} />
        <MeasurementInput label='身高' unit='cm' value={draft.heightCm} onChange={value => update('heightCm', value)} decimal />
        <MeasurementInput label='体重' unit='kg' value={draft.weightKg} onChange={value => update('weightKg', value)} decimal />
      </View>
    </View>
  )
}

function PreferenceStep ({
  draft,
  update
}: {
  draft: OnboardingDraft
  update: <K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) => void
}) {
  return (
    <View>
      <StepTitle title='你的训练习惯' subtitle='之后可以在个人资料中随时修改。' />
      <Text className='field-title'>常用训练地点</Text>
      <OptionGrid
        options={locationOptions}
        value={draft.location}
        onChange={value => update('location', value)}
      />
      <View className='slider-block'>
        <Text className='slider-label'>每周训练 <Text className='slider-value'>{draft.trainingDays} 天</Text></Text>
        <Slider
          min={1}
          max={7}
          step={1}
          value={draft.trainingDays}
          activeColor='#1d6b49'
          blockColor='#1d6b49'
          onChange={event => update('trainingDays', event.detail.value)}
        />
      </View>
      <Text className='field-title'>单次训练时长</Text>
      <OptionGrid
        options={['30', '45', '60', '90'].map(value => [value, `${value} 分钟`])}
        value={String(draft.sessionDuration)}
        onChange={value => update('sessionDuration', Number(value))}
      />
    </View>
  )
}

function HealthStep ({
  draft,
  onToggle
}: {
  draft: OnboardingDraft
  onToggle: (key: 'injuries' | 'chronicConditions', value: string) => void
}) {
  return (
    <View>
      <StepTitle title='健康与运动安全' subtitle='请选择“无”或所有符合的项目。' />
      <Text className='field-title'>伤病或经常不适的部位</Text>
      <MultiOptions
        options={injuryOptions}
        values={draft.injuries}
        onToggle={value => onToggle('injuries', value)}
      />
      <Text className='field-title health-second-title'>慢性疾病或需要关注的情况</Text>
      <MultiOptions
        options={chronicOptions}
        values={draft.chronicConditions}
        onToggle={value => onToggle('chronicConditions', value)}
      />
      <View className='safety-notice'>
        这些信息只用于训练安全提示，不能替代医生诊断。运动中出现胸痛、眩晕、异常呼吸困难时，请立即停止并寻求医疗帮助。
      </View>
    </View>
  )
}

function ConfirmStep ({
  draft,
  update
}: {
  draft: OnboardingDraft
  update: <K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) => void
}) {
  return (
    <View>
      <StepTitle title='最后确认' subtitle='饮食偏好会用于后续营养建议。' />
      <Text className='field-title'>饮食偏好</Text>
      <OptionGrid
        options={dietOptions}
        value={draft.dietRestriction}
        onChange={value => update('dietRestriction', value)}
        vertical
      />
      <View
        className={`acknowledgement ${draft.safetyAcknowledged ? 'selected' : ''}`}
        onClick={() => update('safetyAcknowledged', !draft.safetyAcknowledged)}
      >
        <View className='check-box'>{draft.safetyAcknowledged ? '✓' : ''}</View>
        <Text>我已确认资料真实，并理解训练建议不替代专业医疗意见。</Text>
      </View>
    </View>
  )
}

function ChoiceStep ({
  title,
  subtitle,
  value,
  options,
  onChange,
  vertical = false
}: {
  title: string
  subtitle: string
  value: string
  options: string[][]
  onChange: (value: string) => void
  vertical?: boolean
}) {
  return (
    <View>
      <StepTitle title={title} subtitle={subtitle} />
      <OptionGrid options={options} value={value} onChange={onChange} vertical={vertical} />
    </View>
  )
}

function StepTitle ({ title, subtitle }: { title: string, subtitle: string }) {
  return (
    <View className='step-title-block'>
      <Text className='step-title'>{title}</Text>
      <Text className='step-subtitle'>{subtitle}</Text>
    </View>
  )
}

function OptionGrid ({
  options,
  value,
  onChange,
  vertical = false
}: {
  options: string[][]
  value: string
  onChange: (value: string) => void
  vertical?: boolean
}) {
  return (
    <View className={`option-grid ${vertical ? 'vertical' : ''}`}>
      {options.map(([optionValue, label]) => (
        <View
          key={optionValue}
          className={`choice-option ${value === optionValue ? 'selected' : ''}`}
          onClick={() => onChange(optionValue)}
        >
          {label}
        </View>
      ))}
    </View>
  )
}

function MultiOptions ({
  options,
  values,
  onToggle
}: {
  options: string[]
  values: string[]
  onToggle: (value: string) => void
}) {
  return (
    <View className='multi-options'>
      {options.map(value => (
        <View
          key={value}
          className={`multi-option ${values.includes(value) ? 'selected' : ''}`}
          onClick={() => onToggle(value)}
        >
          {value === 'none' ? '无' : value}
        </View>
      ))}
    </View>
  )
}

function MeasurementInput ({
  label,
  unit,
  value,
  onChange,
  decimal = false
}: {
  label: string
  unit: string
  value: string
  onChange: (value: string) => void
  decimal?: boolean
}) {
  return (
    <View className='measurement-field'>
      <Text className='measurement-label'>{label}</Text>
      <View className='measurement-input-wrap'>
        <Input
          className='measurement-input'
          type={decimal ? 'digit' : 'number'}
          value={value}
          placeholder='--'
          onInput={event => onChange(event.detail.value)}
        />
        <Text className='measurement-unit'>{unit}</Text>
      </View>
    </View>
  )
}

function draftFromProfile(profile: UserProfile): OnboardingDraft {
  return {
    ...defaultDraft,
    gender: profile.gender || '',
    age: profile.age != null ? profile.age.toString() : '',
    heightCm: profile.height_cm != null ? profile.height_cm.toString() : '',
    weightKg: profile.weight_kg != null ? profile.weight_kg.toString() : '',
    goal: profile.primary_goal || '',
    experience: profile.experience_level || '',
    location: profile.training_location || '',
    trainingDays: profile.training_days_per_week || 3,
    sessionDuration: profile.session_duration_min || 45,
    injuries: profile.injuries.length ? profile.injuries : [],
    chronicConditions: profile.chronic_conditions.length ? profile.chronic_conditions : [],
    dietRestriction: profile.diet_restriction || 'none'
  }
}

function validateStep(step: number, draft: OnboardingDraft): string {
  if (step === 0) {
    const age = Number(draft.age)
    const height = Number(draft.heightCm)
    const weight = Number(draft.weightKg)
    if (!draft.gender) return '请选择性别或“暂不透露”'
    if (!Number.isInteger(age) || age < 12 || age > 100) return '请输入 12–100 岁之间的有效年龄'
    if (!Number.isFinite(height) || height < 100 || height > 250) return '请输入 100–250 cm 之间的有效身高'
    if (!Number.isFinite(weight) || weight < 25 || weight > 350) return '请输入 25–350 kg 之间的有效体重'
  }
  if (step === 1 && !draft.goal) return '请选择主要训练目标'
  if (step === 2 && !draft.experience) return '请选择训练经验'
  if (step === 3 && !draft.location) return '请选择常用训练地点'
  if (step === 4 && (!draft.injuries.length || !draft.chronicConditions.length)) {
    return '请分别选择伤病情况和慢性疾病情况；没有时请选择“无”'
  }
  if (step === 5 && !draft.safetyAcknowledged) return '请先阅读并确认安全说明'
  return ''
}
