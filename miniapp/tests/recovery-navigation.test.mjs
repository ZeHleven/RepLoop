import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'

for (const status of ['pending_confirmation', 'applied', 'rejected', 'expired', 'stale']) {
  test(`history ${status} navigates to the real proposal, refreshes status, and never writes`, async () => {
    let current = status, failed = false, show
    const navigations = []
    const page = runtime('../../src/pages/history/index.tsx', {
      '@tarojs/taro': { __esModule: true, useDidShow: fn => { show = fn }, useDidHide: () => {}, default: { navigateTo: async value => { navigations.push(value) } } },
      '../../services/workouts': { workoutApi: {
        history: async () => {
          if (failed) throw new Error('刷新失败')
          return [{ id: 's', trained_at: '2026-09-07', status: 'completed', exercises: [], total_sets: 0, total_reps: 0, total_volume_kg: 0,
            adjustments: [{ exercise_id: 'e', reason: '建议', exercise_name: '卧推' }],
            adaptive_adjustment_status: current, adaptive_adjustment_proposal: { id: 'p', status: current } }]
        }, progress: async () => null
      } }, '../../core/request': { errorMessage: e => e.message }
    })
    page.render(); show(); await page.flush()
    await page.click('adaptive-proposal-link')
    assert.deepEqual(navigations, [{ url: '/pages/plan-proposal-detail/index?id=p' }])
    assert.match(page.text(), status === 'pending_confirmation' ? /调整提案/ : /提案结果/)
    current = 'applied'
    // The service is substituted here; real proposal writes invalidate in apiRequest.
    page.loadSource('../../src/core/read-cache.ts').invalidateReadCache()
    show(); await page.flush(); assert.match(page.text(), /已应用/)
    failed = true
    page.loadSource('../../src/core/read-cache.ts').invalidateReadCache()
    show(); await page.flush()
    assert.ok(page.find('history-card')); assert.ok(page.find('history-retry'))
  })
}

test('history switch explicitly abandons local waiting, not the persisted conversation', async () => {
  let selected = 'old', pending = { conversation_id: 'old', client_request_id: 'p' }, show, back = 0
  const page = runtime('../../src/pages/agent-conversations/index.tsx', {
    '@tarojs/taro': { __esModule: true, useDidShow: fn => { show = fn }, default: { navigateBack: async () => { back++ } } },
    '../../core/storage': { getAgentConversationId: () => selected, saveAgentConversationId: id => { selected = id }, getPendingAgentRequest: () => pending, clearPendingAgentRequest: () => { pending = null } },
    '../../services/agent': { agentApi: { conversations: async () => [{ id: 'new', title: '另一个会话', updated_at: '2026-09-07T12:00:00Z' }] } },
    '../../core/request': { errorMessage: e => e.message }
  })
  page.render(); show(); await page.flush()
  await page.click('conversation-card'); await page.flush()
  assert.equal(back, 0); assert.equal(selected, 'old'); assert.ok(pending)
  page.click('keep-current-conversation'); await page.flush()
  assert.equal(page.find('conversation-switch-prompt'), undefined)
  await page.click('conversation-card'); await page.flush()
  await page.click('confirm-history-switch')
  assert.equal(back, 1); assert.equal(selected, 'new'); assert.equal(pending, null)
})
