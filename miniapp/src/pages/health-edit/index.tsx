import { useState } from 'react'
import { Button, Input, Picker, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { profileApi } from '../../services/profile'
import './index.scss'

const injuryOptions = ['膝关节', '肩关节', '腰背部', '踝关节', '腕肘部']
const conditionOptions = ['高血压', '心血管疾病', '糖尿病', '哮喘', '骨质疏松']
const dietOptions = [
  { value: 'none', label: '无特殊限制' },
  { value: 'vegetarian', label: '素食' },
  { value: 'vegan', label: '纯素' },
  { value: 'halal', label: '清真' },
  { value: 'gluten_free', label: '无麸质' },
  { value: 'lactose_free', label: '无乳糖' }
]

export default function HealthEditPage () {
  const [injuries, setInjuries] = useState<string[]>([])
  const [conditions, setConditions] = useState<string[]>([])
  const [diet, setDiet] = useState('none')
  const [customInjury, setCustomInjury] = useState('')
  const [customCondition, setCustomCondition] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useLoad(() => {
    void profileApi.get().then(profile => {
      setInjuries(profile.injuries)
      setConditions(profile.chronic_conditions)
      setDiet(profile.diet_restriction || 'none')
    }).catch(requestError => {
      setError(errorMessage(requestError, '健康资料加载失败'))
    }).finally(() => setLoading(false))
  })

  const toggle = (value: string, values: string[], setValues: (values: string[]) => void) => {
    setValues(values.includes(value) ? values.filter(item => item !== value) : [...values, value])
  }

  const addCustom = (
    value: string,
    values: string[],
    setValues: (values: string[]) => void,
    clear: () => void
  ) => {
    const normalized = value.trim()
    if (!normalized || values.includes(normalized)) return
    if (normalized.length > 50) {
      setError('单项健康描述不能超过 50 个字符')
      return
    }
    setValues([...values, normalized])
    clear()
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const result = await profileApi.update({
        injuries,
        chronic_conditions: conditions,
        diet_restriction: diet
      })
      if (result.active_plan_safety_status === 'needs_review') {
        await Taro.showModal({
          title: '健康资料已保存',
          content: `当前计划需要复核：${(result.active_plan_safety_reasons || []).join('；')}。复核前无法开始新的训练。`,
          showCancel: false,
          confirmText: '我知道了'
        })
      } else {
        await Taro.showToast({ title: '健康资料已保存', icon: 'success' })
      }
      await Taro.navigateBack()
    } catch (requestError) {
      setError(errorMessage(requestError, '健康资料保存失败'))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <View className='loading-state'>正在加载健康资料…</View>
  const dietIndex = Math.max(0, dietOptions.findIndex(item => item.value === diet))

  return (
    <View className='page health-edit-page'>
      <Text className='health-title'>健康与饮食偏好</Text>
      <Text className='health-subtitle'>请记录真实情况。若现有计划出现不兼容动作，系统会阻止开始新训练，但不会替你自动修改计划。</Text>
      {error && <View className='error-banner'>{error}</View>}

      <View className='card health-card'>
        <Text className='field-title'>伤病或需要避开的部位</Text>
        <TagOptions options={injuryOptions} values={injuries} onToggle={value => toggle(value, injuries, setInjuries)} />
        <SelectedTags values={injuries.filter(value => !injuryOptions.includes(value))} onRemove={value => toggle(value, injuries, setInjuries)} />
        <View className='custom-row'>
          <Input className='custom-input' value={customInjury} placeholder='补充其他伤病' onInput={event => setCustomInjury(event.detail.value)} />
          <Button size='mini' className='add-button' onClick={() => addCustom(customInjury, injuries, setInjuries, () => setCustomInjury(''))}>添加</Button>
        </View>

        <Text className='field-title'>慢性疾病或长期情况</Text>
        <TagOptions options={conditionOptions} values={conditions} onToggle={value => toggle(value, conditions, setConditions)} />
        <SelectedTags values={conditions.filter(value => !conditionOptions.includes(value))} onRemove={value => toggle(value, conditions, setConditions)} />
        <View className='custom-row'>
          <Input className='custom-input' value={customCondition} placeholder='补充其他情况' onInput={event => setCustomCondition(event.detail.value)} />
          <Button size='mini' className='add-button' onClick={() => addCustom(customCondition, conditions, setConditions, () => setCustomCondition(''))}>添加</Button>
        </View>

        <Text className='field-title'>饮食偏好</Text>
        <Picker mode='selector' range={dietOptions.map(item => item.label)} value={dietIndex} onChange={event => setDiet(dietOptions[Number(event.detail.value)].value)}>
          <View className='diet-picker'>{dietOptions[dietIndex].label}<Text>⌄</Text></View>
        </Picker>
      </View>

      <View className='health-notice'>出现胸痛、呼吸困难、晕厥或剧烈疼痛时，请停止训练并及时寻求医疗帮助；本功能不能替代医生诊断。</View>
      <Button className='primary-button' loading={saving} disabled={saving} onClick={save}>保存健康资料</Button>
    </View>
  )
}

function TagOptions ({ options, values, onToggle }: { options: string[], values: string[], onToggle: (value: string) => void }) {
  return (
    <View className='health-tags'>
      {options.map(value => (
        <View className={`health-tag ${values.includes(value) ? 'selected' : ''}`} key={value} onClick={() => onToggle(value)}>{value}</View>
      ))}
    </View>
  )
}

function SelectedTags ({ values, onRemove }: { values: string[], onRemove: (value: string) => void }) {
  if (!values.length) return null
  return (
    <View className='selected-custom'>
      {values.map(value => <View className='custom-tag' key={value} onClick={() => onRemove(value)}>{value} ×</View>)}
    </View>
  )
}
