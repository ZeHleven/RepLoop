import {chromium} from 'file:///C:/Users/12521/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
import {mkdir,writeFile} from 'node:fs/promises'
import assert from 'node:assert/strict'
import {loadingFixtures,fixtureResponse} from '../tests/helpers/loading-fixtures.mjs'
await mkdir('qa/navigation',{recursive:true})
const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true})
const page=await browser.newPage({viewport:{width:393,height:852},deviceScaleFactor:1,isMobile:true,hasTouch:true})
const errors=[],requests=[],checks=[],data=loadingFixtures()
page.on('pageerror',e=>errors.push(e.message))
await page.route('**/*',async route=>{
 const req=route.request(),url=new URL(req.url());if(url.origin==='http://127.0.0.1:8650')return route.continue()
 assert.equal(url.origin,'http://127.0.0.1:18541');assert.equal(req.method(),'GET')
 requests.push(url.pathname)
 const pathname=url.pathname.replace('/api/v1','')
 let response=fixtureResponse(data,pathname)
 if(pathname==='/workouts/plans')response=Array.from({length:5},(_,i)=>({...data.plan,id:`plan-${i}`,name:`长列表训练计划 ${i+1}`}))
 if(pathname==='/meals/history')response=Array.from({length:6},(_,i)=>({...data.today,date:`2026-09-${String(6-i).padStart(2,'0')}`,meals:data.today.meals.map((meal,j)=>({...meal,id:`history-${i}-${j}`}))}))
 await route.fulfill({json:response,headers:{'Access-Control-Allow-Origin':'*'}})
})
await page.addInitScript(()=>{localStorage.setItem('fitness_access_token',JSON.stringify('fixture-only'));localStorage.setItem('fitness_refresh_token',JSON.stringify('fixture-only'))})
const check=(name,result)=>{assert.ok(result,name);checks.push(name)}
const settle=()=>page.waitForTimeout(450)
const shot=async name=>{await settle();await page.screenshot({path:`qa/navigation/${name}.png`})}
try {
 await page.goto('http://127.0.0.1:8650/#/pages/workouts/index',{waitUntil:'networkidle'})
 await page.addStyleTag({content:'body,.taro_page,.taro_router,.page{background:#f5f7f2!important;color:#173226;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif!important;font-size:16px;line-height:1.55} taro-view-core,taro-text-core{font-family:inherit!important}taro-button-core[disabled="false"]{opacity:1!important}'})
 await shot('workouts');check('custom navigation rendered',await page.locator('.capsule-bar').count()===1)
 for(const [i,label]of [[1,'nutrition'],[2,'agent'],[3,'me'],[0,'workouts']]){
   await page.locator('.capsule-item').nth(i).click();await settle();check(label+' selected',await page.locator('.capsule-selected').innerText()===(['训练','饮食','Agent','我的'][i]));await shot(label)
 }
 await page.locator('.capsule-item').nth(2).click();await settle()
 const composer=page.locator('.composer'),bar=page.locator('.capsule-bar');const cb=await composer.boundingBox(),bb=await bar.boundingBox()
 check('agent composer clears navigation',cb.y+cb.height<=bb.y)
 const input=page.locator('.composer-input textarea');await input.fill('未发送的草稿');await settle();check('focused composer hides navigation',await page.locator('.capsule-hidden').count()===1)
 await input.blur();await page.locator('.capsule-item').nth(0).click();await settle();await page.locator('.capsule-item').nth(2).click();await settle()
 check('agent draft retained',await input.inputValue()==='未发送的草稿')
 await page.locator('.capsule-item').nth(1).click();await settle();await page.locator('.edit-meal').first().click();await settle()
 const amount=page.locator('.selected-amount-input input').first();await amount.fill('150');await amount.blur()
 await page.locator('.capsule-item').nth(3).click();await settle();await page.locator('.capsule-item').nth(1).click();await settle()
 check('meal 150g unsaved draft retained',await amount.inputValue()==='150')
 for(const width of [320,393,430]) {
  await page.setViewportSize({width,height:width===320?640:852})
  for(const [index,name] of [[0,'workouts'],[1,'nutrition']]) {
   await page.locator('.capsule-item').nth(index).click();await settle()
   const scroll=page.locator(`.${name}-scroll`)
   const bounds=await scroll.boundingBox(),nav=await bar.boundingBox()
   check(`${name} ${width}px viewport ends above navigation`,bounds.y+bounds.height<nav.y)
   const long=await scroll.evaluate(el=>el.scrollHeight>el.clientHeight)
   check(`${name} ${width}px has long content`,long)
   await scroll.evaluate(el=>{el.scrollTop=0})
   await settle()
   const frame=scroll.locator('..'),topEdge=frame.locator('.scroll-edge-top'),bottomEdge=frame.locator('.scroll-edge-bottom')
   check(`${name} ${width}px top edge clear`,(await topEdge.boundingBox()).height===0)
   check(`${name} ${width}px bottom fade present`,(await bottomEdge.boundingBox()).height>0)
   check(`${name} ${width}px decorative overlay does not intercept`,await bottomEdge.evaluate(el=>getComputedStyle(el).pointerEvents)==='none')
   await page.mouse.move(bounds.x+bounds.width/2,bounds.y+100)
   await page.mouse.wheel(0,260);await settle()
   check(`${name} ${width}px accepts user scrolling`,await scroll.evaluate(el=>el.scrollTop>0))
   for(const fraction of [.5,1]) {
    await scroll.evaluate((el,f)=>{el.scrollTop=(el.scrollHeight-el.clientHeight)*f},fraction);await settle()
    check(`${name} ${width}px top fade while scrolled`,(await topEdge.boundingBox()).height>0)
    check(`${name} ${width}px bottom fade reflects remaining content`,fraction===1?(await bottomEdge.boundingBox()).height===0:(await bottomEdge.boundingBox()).height>0)
    const escaped=await page.evaluate(({name,y})=>{
      const hit=document.elementFromPoint(innerWidth/2,y)
      return Boolean(hit?.closest(`.${name}-scroll`))
    },{name,y:(bounds.y+bounds.height+nav.y)/2})
    check(`${name} ${width}px ${fraction===1?'bottom':'middle'} content clipped before bar`,!escaped)
   }
   const last=await scroll.locator(`.${name==='workouts'?'workouts-page':'nutrition-page'} > *`).last().boundingBox()
   check(`${name} ${width}px last content fully above bar`,last.y+last.height<=nav.y && last.y>=bounds.y)
   await shot(`${name}-bottom-${width}`)
   if(width===393){await scroll.evaluate(el=>{el.scrollTop=220});await settle();await shot(`${name}-fade-middle`);await bar.screenshot({path:`qa/navigation/${name}-compact-nav.png`})}
  }
 }
 await page.locator('.capsule-item').nth(1).click();await settle()
 await page.locator('.food-add').first().click();await settle()
 await page.waitForFunction(()=>{
  const editor=document.querySelector('.meal-editor')?.getBoundingClientRect()
  const viewport=document.querySelector('.nutrition-scroll')?.getBoundingClientRect()
  return editor && viewport && Math.abs(editor.y-viewport.y)<6
 },null,{timeout:3000})
 const editor=await page.locator('.meal-editor').boundingBox(),viewport=await page.locator('.nutrition-scroll').boundingBox()
 check('adding food targets bounded editor viewport',Math.abs(editor.y-viewport.y)<6)
 check('adding food keeps existing 150g draft',await amount.inputValue()==='150')
 check('no page errors',errors.length===0)
 await writeFile('qa/navigation/results.json',JSON.stringify({checks,errors,requests},null,2));console.log({checks,errors})
}catch(e){await shot('debug');console.log({errors,requests});throw e}finally{await browser.close()}
