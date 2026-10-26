import { useState } from 'react'
import { Button, Input, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'

import { miniappBuildLabel } from '../../core/build-info'
import { errorMessage } from '../../core/request'
import {
  authenticate,
  authenticateWithWeChat,
  routeForProfile,
  type WeChatLoginStage
} from '../../services/auth'
import { profileApi } from '../../services/profile'
import './index.scss'

export default function LoginPage () {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState<'wechat' | 'account' | null>(null)
  const [wechatStage, setWechatStage] = useState<WeChatLoginStage | null>(null)
  const [showAccountLogin, setShowAccountLogin] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const normalizedEmail = email.trim().toLowerCase()
    if (!normalizedEmail.includes('@') || password.length < 8) {
      setError('请填写有效邮箱，密码至少 8 位')
      return
    }
    setSubmitting('account')
    setError('')
    try {
      await authenticate(normalizedEmail, password)
      const profile = await profileApi.get()
      await Taro.reLaunch({ url: routeForProfile(profile.onboarding_completed) })
    } catch (requestError) {
      setError(errorMessage(requestError, '登录失败'))
    } finally {
      setSubmitting(null)
    }
  }

  const loginWithWeChat = async () => {
    setSubmitting('wechat')
    setWechatStage('credential')
    setError('')
    try {
      const result = await authenticateWithWeChat(setWechatStage)
      await Taro.reLaunch({ url: routeForProfile(result.onboarding_completed) })
    } catch (requestError) {
      setError(errorMessage(requestError, '微信登录失败，请重试'))
    } finally {
      setSubmitting(null)
      setWechatStage(null)
    }
  }

  return (
    <View className='login-page page'>
      <View className='login-hero'>
        <View className='login-mark'>练</View>
        <Text className='login-title'>继续你的训练</Text>
        <Text className='login-subtitle'>记录每组表现，看见长期进步</Text>
      </View>

      <View className='login-card card'>
        {error && <View className='error-banner'>{error}</View>}
        <Button
          className='primary-button wechat-login-button'
          disabled={submitting !== null}
          onClick={loginWithWeChat}
        >
          {submitting === 'wechat'
            ? wechatStage === 'backend' ? '正在连接训练服务…' : '正在获取微信凭证…'
            : '微信一键登录'}
        </Button>
        <Text className='identity-note'>仅使用微信身份建立登录态，不会读取头像、昵称或通讯录</Text>
        <Text className='binding-note'>已有邮箱训练记录的用户请暂用邮箱登录，账号绑定将在下一阶段开放</Text>

        <View className='account-divider'>
          <View className='divider-line' />
          <Text>已有账号登录</Text>
          <View className='divider-line' />
        </View>
        <View className='account-toggle' onClick={() => setShowAccountLogin(value => !value)}>
          {showAccountLogin ? '收起邮箱登录' : '使用已有邮箱账号登录'}
        </View>

        {showAccountLogin && (
          <View className='account-fields'>
            <Text className='field-label'>邮箱</Text>
            <Input
              className='field-input'
              type='text'
              value={email}
              placeholder='name@example.com'
              onInput={event => setEmail(event.detail.value)}
            />
            <Text className='field-label'>密码</Text>
            <Input
              className='field-input'
              password
              value={password}
              placeholder='至少 8 位'
              onInput={event => setPassword(event.detail.value)}
            />
            <Button
              className='secondary-button login-submit'
              disabled={submitting !== null}
              onClick={submit}
            >
              {submitting === 'account' ? '请稍候…' : '登录'}
            </Button>
          </View>
        )}
      </View>

      <Text className='wechat-note'>首次登录后需要完成基础资料与健康筛查，用于生成更安全的训练建议</Text>
      <Text className='build-identity'>{miniappBuildLabel()}</Text>
    </View>
  )
}
