import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {interactionRuntime,manualClock} from './helpers/interaction-runtime.mjs'
const {scrollEdgeSizes}=interactionRuntime('../../src/core/scroll-edges.ts').exports
test('edge depth follows native extent, clamps bounce, shrinks near boundaries and supports short content',()=>{
 assert.deepEqual(scrollEdgeSizes(0,1000,500,375),{top:0,bottom:56})
 assert.deepEqual(scrollEdgeSizes(250,1000,500,375),{top:56,bottom:56})
 assert.deepEqual(scrollEdgeSizes(480,1000,500,375),{top:56,bottom:20})
 assert.deepEqual(scrollEdgeSizes(500,1000,500,375),{top:56,bottom:0})
 assert.deepEqual(scrollEdgeSizes(-50,1000,500,375),{top:0,bottom:56})
 assert.deepEqual(scrollEdgeSizes(900,1000,500,375),{top:56,bottom:0})
 assert.deepEqual(scrollEdgeSizes(0,400,500,375),{top:0,bottom:0})
 assert.deepEqual(scrollEdgeSizes(0,500.5,500,375),{top:0,bottom:0})
 assert.deepEqual(scrollEdgeSizes(0,1000,0,375),{top:0,bottom:0})
 assert.deepEqual(scrollEdgeSizes(NaN,1000,500,375),{top:0,bottom:0})
})
function setup(){
 const clock=manualClock(),pending=[];let reads=0,resize,removed=0
 const taro={nextTick:fn=>fn(),onWindowResize:fn=>{resize=fn},offWindowResize:()=>{removed++},createSelectorQuery:()=>{
  reads++;const query={select:()=>query,boundingClientRect:()=>query,scrollOffset:()=>query,exec:fn=>pending.push(fn)};return query
 }}
 const runtime=interactionRuntime('../../src/components/EdgeScrollView.tsx',{'@tarojs/taro':{__esModule:true,default:taro}},clock.globals)
 const props={className:'test-scroll',contentSelector:'.content',visible:true,scrollY:true,children:'stable content',scrollIntoView:'meal-editor'}
 runtime.render(props)
 return {...runtime,props,clock,pending,reads:()=>reads,resize:()=>resize(),removed:()=>removed,
  answer:(height=1000,top=0)=>pending.shift()?.([{height:500,width:375},{scrollTop:top},{height}])}
}
test('measurement and native scroll set decorative edges without repeated queries; anchor props survive',async()=>{
 const s=setup();s.answer();await s.flush()
 assert.equal(s.find('scroll-edge-bottom').props.style.height,'56px')
 assert.equal(s.find('test-scroll').props.scrollIntoView,'meal-editor')
 const count=s.reads()
 s.invoke('test-scroll','onScroll',{detail:{scrollTop:480,scrollHeight:1000}});await s.flush()
 assert.equal(s.find('scroll-edge-bottom').props.style.height,'20px');assert.equal(s.reads(),count)
 s.clock.advance(350);s.answer(500);await s.flush();assert.equal(s.find('scroll-edge-bottom').props.style.height,'0px')
 s.unmount();assert.equal(s.clock.count(),0);assert.equal(s.removed(),1)
})
test('hidden/unmounted pages reject delayed measurement and clean resize listener',async()=>{
 const s=setup();const old=s.pending.shift();s.render({...s.props,visible:false});old([{height:500,width:375},{scrollTop:250},{height:1000}]);await s.flush()
 assert.equal(s.find('scroll-edge-top').props.style.height,'0px');assert.equal(s.clock.count(),0)
 s.render(s.props);s.answer(400);await s.flush();assert.equal(s.find('scroll-edge-bottom').props.style.height,'0px')
 s.resize();assert.ok(s.pending.length);s.unmount();s.pending.shift()([{height:500,width:375},{scrollTop:250},{height:1000}]);assert.equal(s.clock.count(),0)
})
test('latest layout wins and failed measurement remains non-blocking',async()=>{
 const s=setup();const stale=s.pending.shift();s.resize();s.answer(400);stale([{height:500,width:375},{scrollTop:0},{height:1000}]);await s.flush()
 assert.equal(s.find('scroll-edge-bottom').props.style.height,'0px');s.unmount()
 const fallback=interactionRuntime('../../src/components/EdgeScrollView.tsx');fallback.render(s.props);await fallback.flush();assert.equal(fallback.find('test-scroll').type,'ScrollView');fallback.unmount()
})
test('compact geometry and noninteractive overlays are static, no infinite animation or blur',()=>{
 const styles=readFileSync(new URL('../src/components/EdgeScrollView.scss',import.meta.url),'utf8')
 assert.match(styles,/pointer-events:none/);assert.doesNotMatch(styles,/infinite|backdrop-filter/)
 const layout=readFileSync(new URL('../src/styles/_capsule-layout.scss',import.meta.url),'utf8')
 assert.match(layout,/\$height: 128px/);assert.match(layout,/\$content-gap: 16px/)
 const nav=readFileSync(new URL('../src/custom-tab-bar/index.scss',import.meta.url),'utf8')
 assert.match(nav,/left:48px;right:48px/);assert.match(nav,/font-size:23px/);assert.match(nav,/width:44px;height:44px/)
})
