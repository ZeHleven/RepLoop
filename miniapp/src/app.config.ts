import { navigationTheme } from './core/navigation-theme'

export default defineAppConfig({
  usingComponents: {},
  pages: [
    'pages/index/index',
    'pages/login/index',
    'pages/onboarding/index',
    'pages/plan-builder/index',
    'pages/proposal-detail/index',
    'pages/plan-editor/index',
    'pages/plan-proposal-detail/index',
    'pages/domain-proposal-detail/index',
    'pages/workouts/index',
    'pages/nutrition/index',
    'pages/agent/index',
    'pages/agent-conversations/index',
    'pages/me/index',
    'pages/profile-edit/index',
    'pages/health-edit/index',
    'pages/weight/index',
    'pages/workout-active/index',
    'pages/history/index',
    'pages/workout-detail/index'
  ],
  tabBar: {
    custom: true,
    color: '#708077',
    selectedColor: '#1d6b49',
    backgroundColor: '#ffffff',
    borderStyle: 'white',
    list: [
      { pagePath: 'pages/workouts/index', text: '训练' },
      { pagePath: 'pages/nutrition/index', text: '饮食' },
      { pagePath: 'pages/agent/index', text: 'Agent' },
      { pagePath: 'pages/me/index', text: '我的' }
    ]
  },
  window: {
    backgroundTextStyle: 'dark',
    backgroundColor: '#f5f7f2',
    navigationBarBackgroundColor: navigationTheme.backgroundColor,
    navigationBarTitleText: '训练搭子',
    navigationBarTextStyle: navigationTheme.textStyle
  }
})
