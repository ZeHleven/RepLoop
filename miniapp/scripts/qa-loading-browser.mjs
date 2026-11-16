// Disposable browser only. Never reads an existing browser profile or writes business data.
// Usage: node scripts/qa-loading-browser.mjs fixture|live
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'file:///C:/Users/12521/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
import { fixtureResponse, loadingFixtures } from '../tests/helpers/loading-fixtures.mjs'

const mode = process.argv[2] || 'fixture'
assert.ok(['fixture', 'live'].includes(mode))
const output = path.resolve('qa', mode)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true })
const backend = 'http://192.168.0.7:8541'
const result = { mode, clock: 'ms: visible content; settledMs: reads settled; fixture first load includes screenshots; not native WeChat', samples: [], errors: [] }
let access = '', refresh = ''
try {
  if (mode === 'live') {
    const request = await browser.newContext()
    const info = await (await request.request.get(`${backend}/test-info`)).json()
    assert.equal(info.test_id, 'agent-acceptance-20260910')
    const source = await readFile(path.resolve('../../agent-acceptance-test-20260910/acceptance.py'), 'utf8')
    const credentials = source.match(/^EMAIL, PASSWORD = "([^"]+)", "([^"]+)"/m)
    assert.ok(credentials, 'Synthetic account must exist in the original isolated harness')
    const response = await request.request.post(`${backend}/api/v1/auth/login`, { data: { email: credentials[1], password: credentials[2] } })
    assert.equal(response.status(), 200)
    const tokens = await response.json(); access = tokens.access_token; refresh = tokens.refresh_token
    assert.ok(access && refresh); await request.close()
  }
  for (const iteration of Array.from({length:mode === 'live' ? 3 : 1}, (_, i) => i + 1)) {
  for (const [label, port] of [['baseline',8645],['optimized',8646]]) {
    const context = await browser.newContext({ viewport: { width:393, height:852 }, isMobile:true, hasTouch:true, deviceScaleFactor:1 })
    const calls = [], data = loadingFixtures()
    let slow = mode === 'fixture' ? 900 : 0, fail = false
    const page = await context.newPage()
    page.on('pageerror', e => result.errors.push({ label, message:e.message }))
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin === `http://127.0.0.1:${port}`) return route.continue()
      if (url.origin !== 'http://127.0.0.1:18541' || request.method() !== 'GET') return route.abort()
      const endpoint = url.pathname.replace('/api/v1','')
      const allowed = /^\/(profile(?:\/weight)?|workouts\/(plans|sessions(?:\/(active|progress|[a-zA-Z0-9-]+))?)|meals\/(today|history)|foods\/library)$/
      assert.match(endpoint, allowed)
      const call = { endpoint: endpoint.replace(/sessions\/[a-f0-9-]{24,}/, 'sessions/:id'), ms:0, status:0 }
      calls.push(call)
      const start = performance.now()
      if (slow) await new Promise(resolve => setTimeout(resolve, slow))
      let body, status = 200
      if (fail && endpoint.startsWith('/meals/')) { status = 503; body = {detail:'离线测试，原内容保留'} }
      else if (mode === 'live') {
        const res = await context.request.get(`${backend}${url.pathname}${url.search}`, { headers:{Authorization:`Bearer ${access}`} })
        status = res.status(); body = await res.json()
      } else body = fixtureResponse(data, endpoint)
      call.ms = Math.round(performance.now()-start); call.status=status
      await route.fulfill({status, json:body, headers:{'Access-Control-Allow-Origin':'*'}})
    })
    await page.addInitScript(({access, refresh}) => {
      // API adapter supplies auth. Storage only avoids an application-side login redirect.
      localStorage.setItem('fitness_access_token', JSON.stringify(access || 'fixture-only'))
      localStorage.setItem('fitness_refresh_token', JSON.stringify(refresh || 'fixture-refresh'))
      document.addEventListener('click', () => { window.__qaClickAt = performance.now() }, true)
    }, {access, refresh})
    const visiblePage = name => page.locator(`.${name}-page`).filter({visible:true})
    const waitReady = async (name, selector) => {
      await visiblePage(name).locator(selector).first().waitFor()
      await page.waitForFunction(name => {
        const roots = [...document.querySelectorAll(`.${name}-page`)]
        const root = roots.find(el => el.getBoundingClientRect().width > 0)
        return root && !root.querySelector('.read-wave') && !/正在加载|正在整理/.test(root.textContent)
      }, name)
    }
    const sample = async (name, phase, action, ready) => {
      const count = calls.length, start = performance.now()
      await action(); await waitReady(name, ready)
      // Let immediate effects/requests settle; excluded from reported content time.
      const ms = Math.round(performance.now()-start)
      const afterClickMs = await page.evaluate(() => window.__qaClickAt == null ? null : Math.round(performance.now()-window.__qaClickAt))
      await page.waitForTimeout(100)
      while (calls.slice(count).some(c => c.status === 0)) await page.waitForTimeout(20)
      result.samples.push({label, iteration, name, phase, ms, afterClickMs, settledMs:Math.round(performance.now()-start), requests:calls.slice(count)})
    }
    await sample('workouts','first', async () => {
      await page.goto(`http://127.0.0.1:${port}/#/pages/workouts/index`)
      // H5 host adapter for native page selectors, QA only; not shipped in the miniapp.
      await page.addStyleTag({content:'body,.taro_page,.taro_router,.page{background:#f5f7f2!important;color:#173226;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif!important;font-size:16px;line-height:1.55} taro-view-core,taro-text-core{font-family:inherit!important} .weui-tabbar__icon{display:none}'})
      if(label==='optimized' && mode==='fixture') {
        await page.locator('.read-wave-char').first().waitFor()
        await page.waitForTimeout(450)
        await page.screenshot({path:path.join(output,'01-workouts-waiting.png')})
        const letter = page.locator('.read-wave-char').nth(2)
        const first = await letter.evaluate(el => ({transform:getComputedStyle(el).transform,color:getComputedStyle(el).color}))
        await page.waitForTimeout(80)
        const second = await letter.evaluate(el => ({transform:getComputedStyle(el).transform,color:getComputedStyle(el).color}))
        result.wave = {first,second}
        assert.notDeepEqual(first,second)
      }
    }, '.plan-card')
    slow = mode === 'fixture' ? 200 : 0
    const tab = name => page.locator('.weui-tabbar__item').filter({hasText:name}).click()
    await sample('nutrition','first',()=>tab('饮食'),'.daily-card')
    await sample('workouts','repeat',()=>tab('训练'),'.plan-card')
    await sample('nutrition','repeat',()=>tab('饮食'),'.daily-card')
    await sample('me','first',()=>tab('我的'),'.profile-summary')
    await tab('训练'); await waitReady('workouts','.plan-card')
    await sample('me','repeat',()=>tab('我的'),'.profile-summary')
    await tab('训练'); await waitReady('workouts','.plan-card')
    await sample('history','first',()=>page.locator('.history-link:visible').click(),'.history-card')
    await sample('workout-detail','first',()=>page.locator('.workout-detail-link:visible').first().click(),'.detail-heading')
    await page.screenshot({path:path.join(output,`${label}-detail.png`),fullPage:true})
    await page.goBack(); await waitReady('history','.history-card')
    await sample('workout-detail','repeat',()=>page.locator('.workout-detail-link:visible').first().click(),'.detail-heading')
    await page.goBack(); await waitReady('history','.history-card')
    await page.goBack(); await waitReady('workouts','.plan-card')
    await sample('history','repeat',()=>page.locator('.history-link:visible').click(),'.history-card')
    if(label==='optimized') {
      assert.ok(result.samples.filter(s => s.label===label && s.phase==='repeat').every(s=>s.requests.length===0), 'Warm returns must not issue new reads')
      await page.screenshot({path:path.join(output,'02-history-ready.png'),fullPage:true})
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
      assert.equal(overflow,false)
      if (mode === 'fixture') {
        await page.goBack(); await waitReady('workouts','.plan-card')
        await tab('饮食'); await waitReady('nutrition','.daily-card')
        await page.locator('.edit-meal:visible').first().click()
        const input = page.locator('.selected-amount-input input:visible').first()
        await input.fill('150'); await input.blur()
        await page.waitForTimeout(650)
        slow=900; fail=true
        await page.locator('.refresh-meals:visible').click()
        await page.locator('.daily-card:visible').scrollIntoViewIfNeeded()
        await page.waitForTimeout(300)
        assert.ok(await page.locator('.daily-card:visible').count())
        await page.screenshot({path:path.join(output,'03-refresh-keeps-content.png')})
        await page.locator('.today-retry:visible').waitFor()
        assert.equal(await input.inputValue(),'150')
        assert.equal(await page.locator('.read-wave:visible').count(),0)
        await page.screenshot({path:path.join(output,'04-offline-keeps-draft.png'),fullPage:true})
        fail=false
        await page.locator('.today-retry:visible').click()
        await waitReady('nutrition','.daily-card')
        assert.equal(await input.inputValue(),'150')
        await page.locator('.refresh-meals:visible').click()
        await tab('我的')
        assert.equal(await page.locator('.nutrition-page .read-wave').count(),0, 'Hidden page must unmount its wave')
        result.fixtureRecovery = { retainedContent:true, retained150gDraft:true, retry:true, hiddenWaveStopped:true }
        await page.waitForTimeout(1000)
      }
    }
    await context.close()
  }
  }
  assert.deepEqual(result.errors,[])
} finally {
  await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2)+'\n')
  await browser.close()
}
console.log(JSON.stringify(result,null,2))
