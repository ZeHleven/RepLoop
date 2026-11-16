import test from 'node:test'
import assert from 'node:assert/strict'
import {interactionRuntime,manualClock} from './helpers/interaction-runtime.mjs'
import {readFileSync} from 'node:fs'
import {validateNativeTabbar} from '../scripts/native-tabbar-contract.mjs'
import {loadingPage} from './helpers/loading-fixtures.mjs'
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve()}
for(const [name,viewport] of [['workouts','workouts-scroll'],['nutrition','nutrition-scroll']]) {
 test(`${name} content stays in a bounded scroll host with native page scrolling disabled`,async()=>{
  const page=loadingPage(name);await page.show()
  assert.equal(page.find(viewport).type,'ScrollView')
  assert.equal(page.find(viewport).props.scrollY,true)
  const config=readFileSync(new URL(`../src/pages/${name}/index.config.ts`,import.meta.url),'utf8')
  assert.match(config,/disableScroll: true/)
  const css=readFileSync(new URL(`../src/pages/${name}/index.scss`,import.meta.url),'utf8')
  assert.match(css,/capsule.scroll-viewport/)
 })
}
test('compiled native entry gate catches direct and transitive eager App imports',()=>{
 const fixture=dependency=>({appConfig:{tabBar:{custom:true}},javascriptFiles:[
  {path:'app.js',content:`wx.chunks.push([[1],{10:function(m,e,r){${dependency}},20:function(m,e,r){r(30)},30:function(){Component({})}},function(r){return r(10)}]);`},
  {path:'custom-tab-bar/index.js',content:'wx.chunks.push([[2],{},function(r){return r(30)}]);'}
 ]})
 assert.equal(validateNativeTabbar(fixture('r(30)'))[0].code,'native_tabbar_eager_import')
 assert.equal(validateNativeTabbar(fixture('r(20)'))[0].code,'native_tabbar_eager_import')
 assert.deepEqual(validateNativeTabbar(fixture('')),[])
})
function setup(){
 const clock=manualClock(),calls=[];let route=0,time=0
 const {createCapsuleNavigation,tabIndex}=interactionRuntime('../../src/core/capsule-navigation.ts',{},clock.globals).exports
 const nav=createCapsuleNavigation({now:()=>time,current:()=>route,hideKeyboard:()=>{},switchTab:index=>new Promise((resolve,reject)=>calls.push({index,resolve:()=>{route=index;nav.show(index);resolve()},reject}))})
 return {nav,calls,clock,tabIndex,setRoute:i=>{route=i;nav.show(i)},advance:ms=>{time+=ms;clock.advance(ms)}}
}
test('same tab does nothing; selected motion terminates',async()=>{
 const s=setup();s.nav.show(0);s.nav.select(0);assert.equal(s.calls.length,0)
 s.nav.select(1);assert.equal(s.nav.snapshot().animate,true);s.calls[0].resolve();await flush();s.advance(340)
 assert.equal(s.nav.snapshot().animate,false);assert.equal(s.clock.count(),0)
})
test('rapid navigation keeps only latest destination; no duplicate queued routes',async()=>{
 const s=setup();s.nav.select(1);s.nav.select(2);s.nav.select(3);assert.equal(s.calls.length,1)
 s.calls[0].resolve();await flush();assert.deepEqual(s.calls.map(x=>x.index),[1,3]);s.calls[1].resolve();await flush()
 assert.equal(s.nav.snapshot().selected,3)
})
test('failure restores actual selected page; next explicit click retries',async()=>{
 const s=setup();s.nav.select(2);s.calls[0].reject();await flush();assert.equal(s.nav.snapshot().selected,0);assert.ok(s.nav.snapshot().error)
 s.nav.select(2);assert.equal(s.calls.length,2);s.calls[1].resolve();await flush()
})
test('programmatic navigation and returning from detail synchronize without replay',()=>{
 const s=setup();s.setRoute(3);assert.equal(s.nav.snapshot().selected,3);assert.equal(s.nav.snapshot().animate,false)
 s.nav.keyboard(250);assert.equal(s.nav.snapshot().keyboard,true);s.setRoute(3);assert.equal(s.nav.snapshot().keyboard,false)
 assert.equal(s.tabIndex('pages/agent/index'),2);assert.equal(s.tabIndex('/pages/me/index?x=1'),3);assert.equal(s.tabIndex('/pages/login/index'),-1)
})
test('background cancels delayed intents and presentation timers',async()=>{
 const s=setup();s.nav.select(1);s.nav.select(3);s.nav.pause();s.calls[0].resolve();await flush();assert.equal(s.calls.length,1)
 assert.equal(s.clock.count(),0);s.setRoute(1);assert.equal(s.nav.snapshot().selected,1)
})
test('interrupted movement starts at its current position; new subscribers retain timestamp',()=>{
 const s=setup();s.nav.select(3);s.advance(100);s.nav.select(1)
 const state=s.nav.snapshot();assert.ok(state.from>0&&state.from<3)
 let snapshot;const unsubscribe=s.nav.subscribe(x=>{snapshot=x});assert.equal(snapshot.started,100);unsubscribe();s.nav.dispose();assert.equal(s.clock.count(),0)
})

test('native custom tab component opts into extracted styles and keyboard lifecycle cleanup',async()=>{
 let installed=0,removed=0,keyboard
 const loaded=interactionRuntime('../../src/custom-tab-bar/index.tsx',{'@tarojs/taro':{__esModule:true,default:{onKeyboardHeightChange:fn=>{keyboard=fn;installed++},offKeyboardHeightChange:fn=>{assert.equal(fn,keyboard);removed++}}}})
 assert.equal(loaded.exports.default.options.addGlobalClass,true)
 loaded.render();await loaded.flush();assert.equal(installed,1);assert.equal(loaded.findAll('capsule-item').length,4)
 keyboard({height:280});await loaded.flush();assert.ok(loaded.find('capsule-hidden'))
 keyboard({height:0});await loaded.flush();assert.equal(loaded.find('capsule-hidden'),undefined)
 loaded.unmount();assert.equal(removed,1)
})

test('compiled-entry source retains native tab paths and reserves safe-area space',()=>{
 const config=readFileSync(new URL('../src/app.config.ts',import.meta.url),'utf8')
 assert.match(config,/custom: true/);assert.match(config,/usingComponents: \{\}/)
 for(const name of ['workouts','nutrition','agent','me'])assert.ok(config.includes(`pages/${name}/index`))
 const styles=readFileSync(new URL('../src/custom-tab-bar/index.scss',import.meta.url),'utf8')
 assert.match(styles,/env\(safe-area-inset-bottom\)/);assert.match(styles,/300ms/);assert.doesNotMatch(styles,/infinite/)
})
