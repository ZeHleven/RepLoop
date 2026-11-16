// Actual Taro H5 with browser-local fixtures. Never sends business writes to a server.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'file:///C:/Users/12521/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
import { loadingFixtures } from '../tests/helpers/loading-fixtures.mjs'
await mkdir('qa/energy-scope',{recursive:true})
const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true})
const context=await browser.newContext({viewport:{width:393,height:852},deviceScaleFactor:1,isMobile:true,hasTouch:true})
const data=loadingFixtures();data.session.energy_classification_version=0
data.session.exercises=[0,1].map(i=>({...data.exercise,id:`sx${i}`,exercise_id:`custom${i}`,exercise_name:['哑铃交替弯举','自重深蹲'][i],energy_category:null,energy_classification_editable:true,library_energy_editable:true,library_energy_category:null,library_energy_category_version:0}))
let writes=0, reads=0, lost=false, failRead=false
const errors=[], checks=[], payloads=[]
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
await context.route('**/*',async route=>{
 const req=route.request(),url=new URL(req.url())
 if(url.origin==='http://127.0.0.1:8648')return route.continue()
 assert.equal(url.origin,'http://127.0.0.1:18541')
 const endpoint=url.pathname.replace('/api/v1','')
 if(req.method()==='OPTIONS')return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'GET,PUT,OPTIONS'}})
 if(req.method()==='PUT'){
   assert.equal(endpoint,'/workouts/sessions/s/energy-classifications');writes++
   const body=req.postDataJSON();payloads.push(body)
   data.session.energy_classification_version=body.expected_version+1
   for(const change of body.exercises){const row=data.session.exercises.find(x=>x.id===change.session_exercise_id);row.energy_category=change.energy_category;if(body.update_future){row.library_energy_category=change.energy_category;row.library_energy_category_version=change.expected_exercise_version+1}}
   if(lost)return route.abort('failed')
   await new Promise(r=>setTimeout(r,300))
 }else {assert.equal(req.method(),'GET');assert.equal(endpoint,'/workouts/sessions/s');reads++;if(failRead)return route.abort('failed')}
 return route.fulfill({status:200,json:data.session,headers:{'Access-Control-Allow-Origin':'*'}})
})
await page.addInitScript(()=>{localStorage.setItem('fitness_access_token',JSON.stringify('fixture-only'));localStorage.setItem('fitness_refresh_token',JSON.stringify('fixture-only'))})
const check=(name,value)=>{assert.ok(value,name);checks.push(name)}
const settle=()=>page.waitForTimeout(450)
const shot=async name=>{await settle();await page.screenshot({path:`qa/energy-scope/${name}.png`})}
try{
 await page.goto('http://127.0.0.1:8648/#/pages/workout-detail/index?id=s')
 await page.addStyleTag({content:'body,.taro_page,.taro_router,.page{background:#f5f7f2!important;color:#173226;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif!important;font-size:16px;line-height:1.55} taro-view-core,taro-text-core{font-family:inherit!important}'})
 // H5 serializes disabled=false as an attribute. Restore native boolean semantics for
 // screenshot comparison only; do not ship this host-specific adapter to WeChat.
 await page.addStyleTag({content:'taro-button-core[disabled="false"]{opacity:1!important}.session-energy-option{color:#526245!important}.session-energy-option.is-selected{color:#173f2f!important}.primary-button[disabled="false"]{color:#fff!important}.secondary-button[disabled="false"]{color:#1d6b49!important}.energy-scope-close[disabled="false"]{color:#72816c!important}.session-energy-option:before,.session-energy-option:after{display:none!important}'})
 const card=page.locator('.energy-classification-editor');await card.waitFor();await shot('collapsed')
 check('default collapsed',await card.locator('.workout-disclosure-body').evaluate(e=>e.getBoundingClientRect().height===0))
 await card.locator('.workout-disclosure-heading').click();await settle()
 await card.locator('.session-energy-resistance').first().click();await card.locator('.session-energy-bodyweight').last().click()
 await card.locator('.save-session-energy').scrollIntoViewIfNeeded();await shot('expanded')
 await card.locator('.save-session-energy').click();await page.locator('.energy-scope-panel').waitFor();await shot('dialog')
 check('opening does not write',writes===0)
 const box=await page.locator('.energy-scope-panel').boundingBox();check('modal fits phone viewport',box.x>=0&&box.x+box.width<=394&&box.y>=0&&box.y+box.height<=852)
 const closeBox=await page.locator('.energy-scope-close').boundingBox();check('close target stays at top right',closeBox.width>=44&&closeBox.width<60&&closeBox.x>box.x+box.width/2)
 const motion=await page.locator('.energy-scope-panel').evaluate(e=>({duration:getComputedStyle(e).animationDuration,transform:getComputedStyle(e).transform}));check('220ms fade no scale',motion.duration==='0.22s')
 await page.locator('.energy-scope-close').click();await settle();check('cancel does not write',writes===0)
 check('choices retained',await card.locator('.is-selected').count()===2)
 await card.locator('.save-session-energy').click();await settle();await page.locator('.energy-scope-backdrop').click({position:{x:8,y:15}});await settle();check('backdrop dismisses without write',writes===0)
 await card.locator('.save-session-energy').click();await settle();await page.locator('.energy-scope-current').click();await page.locator('.energy-scope-layer').waitFor({state:'detached'});check('current-only save is one batch',writes===1&&payloads[0].update_future===false&&payloads[0].exercises.length===2)
 check('success keeps card open',await card.locator('.workout-disclosure-body').evaluate(e=>e.getBoundingClientRect().height>0))
 await shot('saved')
 await card.locator('.session-energy-bodyweight').first().click();await card.locator('.save-session-energy').click();await settle();lost=true;failRead=true
 await page.locator('.energy-scope-future').click();await page.locator('.energy-scope-verify').waitFor();await settle();await shot('uncertain')
 check('unknown save blocks replay',writes===2&&await page.locator('.energy-scope-current').evaluate(e=>e.disabled===true))
 failRead=false;await page.locator('.energy-scope-verify').click();await page.locator('.energy-scope-layer').waitFor({state:'detached'})
 check('readback recovers without replay',writes===2&&reads>=3)
 await card.locator('.session-energy-resistance').first().click();await card.locator('.save-session-energy').click();await settle()
 await page.addStyleTag({content:'.energy-scope-title{font-size:26px!important}.energy-scope-description,.energy-scope-help,.energy-scope-actions taro-button-core{font-size:18px!important}.energy-scope-footnote{font-size:15px!important}'})
 await shot('large-font');const large=await page.locator('.energy-scope-panel').boundingBox();check('enlarged modal remains inside viewport',large.y>=0&&large.y+large.height<=852)
 check('no page errors',errors.length===0)
 console.log(JSON.stringify({checks,writes,reads,errors,motion},null,2))
 await writeFile('qa/energy-scope/results.json',JSON.stringify({checks,writes,reads,errors,motion},null,2))
}catch(e){console.log({errors,writes,reads});await page.screenshot({path:'qa/energy-scope/debug.png'});throw e}finally{await browser.close()}
