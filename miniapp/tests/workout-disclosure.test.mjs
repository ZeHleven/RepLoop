import assert from 'node:assert/strict'
import test from 'node:test'
import { interactionRuntime, manualClock } from './helpers/interaction-runtime.mjs'
import { deferred } from './helpers/page-runtime.mjs'

const modulePath = '../../src/core/workout-disclosure.ts'
const tick = () => new Promise(resolve => setImmediate(resolve))
function setup (measure = async () => 240) {
  const clock = manualClock(), snapshots = []
  const { createWorkoutDisclosure } = interactionRuntime(modulePath, {}, clock.globals).exports
  const controller = createWorkoutDisclosure(measure, state => snapshots.push(state))
  return { controller, clock, snapshots }
}
test('disclosure starts closed, measures on opening only and reverses directly to zero', async () => {
  let reads = 0
  const { controller: c, clock } = setup(async () => { reads++; return 240 })
  assert.deepEqual(c.snapshot(), { expanded:false, height:0, opacity:0, duration:350 })
  c.toggle(); await tick()
  assert.deepEqual(c.snapshot(), { expanded:true, height:240, opacity:1, duration:350 })
  c.toggle()
  assert.deepEqual(c.snapshot(), { expanded:false, height:0, opacity:0, duration:350 })
  assert.equal(reads, 1); assert.equal(clock.count(), 0)
})
test('late measurement cannot undo a close or a newer opening', async () => {
  const a=deferred(),b=deferred();let reads=0
  const {controller:c,clock}=setup(()=>++reads===1?a.promise:b.promise)
  c.toggle();c.toggle();c.toggle();b.resolve(320);await tick();a.resolve(100);await tick()
  assert.equal(c.snapshot().height,320);assert.equal(c.snapshot().expanded,true)
  c.toggle();assert.equal(c.snapshot().height,0);assert.equal(clock.count(),0)
})
for(const value of [null,NaN,Infinity,-1,0]) test(`failed measurement ${value} exposes full content rather than clipping`,async()=>{
  const {controller:c,clock}=setup(async()=>value);c.toggle();await tick()
  assert.deepEqual(c.snapshot(),{expanded:true,height:'auto',opacity:1,duration:0})
  c.toggle();assert.equal(c.snapshot().height,0);assert.equal(c.snapshot().duration,0);assert.equal(clock.count(),0)
})
test('hung/rejected measurement degrades safely; timeout and late response cannot replay',async()=>{
  const d=deferred();const {controller:c,clock}=setup(()=>d.promise)
  c.toggle();clock.advance(300);assert.equal(c.snapshot().height,'auto');assert.equal(clock.count(),0)
  c.toggle();d.resolve(900);await tick();assert.equal(c.snapshot().height,0)
  const other=setup(async()=>{throw new Error('host unavailable')});other.controller.toggle();await tick()
  assert.equal(other.controller.snapshot().height,'auto');assert.equal(other.clock.count(),0)
})
test('hide settles motion, ignores stale reads, show remeasures without animation; disposal clears timers',async()=>{
  const d=deferred();let n=0;const {controller:c,clock,snapshots}=setup(()=>++n===1?d.promise:Promise.resolve(280))
  c.toggle();c.setVisible(false);assert.equal(clock.count(),0);assert.equal(c.snapshot().duration,0)
  d.resolve(100);await tick();assert.equal(c.snapshot().height,'auto')
  c.setVisible(true);await tick();assert.equal(c.snapshot().height,280);assert.equal(c.snapshot().duration,0)
  c.toggle();c.toggle();c.dispose();const count=snapshots.length;await tick();clock.advance(1000)
  assert.equal(snapshots.length,count);assert.equal(clock.count(),0)
})
test('content/size refresh measures only an open card and does not change expanded choice',async()=>{
  let size=240,reads=0;const {controller:c}=setup(async()=>{reads++;return size})
  c.refresh();assert.equal(reads,0);c.toggle();await tick();size=430;c.refresh();await tick()
  assert.equal(c.snapshot().height,430);assert.equal(c.snapshot().duration,0);assert.equal(c.snapshot().expanded,true)
})
test('component whole-card tap opens/closes; scroll drag/cancel do not toggle and hidden card suspends',async()=>{
  const clock=manualClock();let measurements=0
  const p=interactionRuntime('../../src/components/WorkoutDisclosure.tsx',{'@tarojs/taro':{__esModule:true,default:{
    nextTick:fn=>queueMicrotask(fn),createSelectorQuery:()=>({select(){return this},boundingClientRect(fn){this.callback=fn;return this},exec(){measurements++;this.callback({height:230})}})
  }}},clock.globals)
  const props={title:'腿举',summary:'3组 × 10次',visible:true,layoutVersion:0,contentKey:'one',children:'完整实际记录'}
  p.render(props);assert.equal(p.find('workout-disclosure-body').props.style.height,'0px')
  p.click('workout-disclosure');await p.flush();assert.equal(p.find('workout-disclosure-body').props.style.height,'230px')
  p.click('workout-disclosure');await p.flush();assert.equal(p.find('workout-disclosure-body').props.style.height,'0px')
  p.invoke('workout-disclosure','onTouchStart',{touches:[{clientX:20,clientY:20}]})
  p.invoke('workout-disclosure','onTouchMove',{touches:[{clientX:20,clientY:90}]});p.click('workout-disclosure');await p.flush()
  assert.equal(p.find('workout-disclosure-body').props.style.height,'0px')
  p.invoke('workout-disclosure','onTouchStart',{touches:[{clientX:20,clientY:20}]});p.invoke('workout-disclosure','onTouchCancel',{});p.click('workout-disclosure');await p.flush()
  assert.equal(p.find('workout-disclosure-body').props.style.height,'0px')
  p.invoke('workout-disclosure','onTouchStart',{touches:[{clientX:20,clientY:20}]});p.click('workout-disclosure');await p.flush()
  p.render({...props,visible:false});await p.flush();assert.equal(p.find('workout-disclosure-body').props.style.transitionDuration,'0ms')
  assert.equal(measurements,2);p.unmount();assert.equal(clock.count(),0)
})
