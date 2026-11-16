import assert from 'node:assert/strict'
import test from 'node:test'
import { interactionRuntime } from './helpers/interaction-runtime.mjs'

const ex=(id,sets=1)=>({id,exercise_id:id,exercise_name:`动作${id}`,target_sets:3,target_reps:'8–12',rest_seconds:90,sets_data:Array.from({length:sets},(_,i)=>({set_number:i+1,reps:8,weight_kg:20,actual_rest_seconds:17})),previous_sets_data:[]})
const record=()=>({id:'s/&',plan_name:'原计划',status:'ended_early',trained_at:'2026-09-11',started_at:'2026-09-11T10:32:00Z',duration_min:38,total_sets:3,total_reps:24,total_volume_kg:480,exercises:[ex('a'),ex('b'),ex('c'),ex('zero',0)],adjustments:[{exercise_id:'a',reason:'完整调整说明'}],adaptive_adjustment_status:'pending_confirmation',adaptive_adjustment_proposal:{id:'p/&'}})
function pageAt(path,data=record()){
 const hooks={},reads=[],navigations=[]
 const p=interactionRuntime(path,{'@tarojs/taro':{__esModule:true,default:{navigateTo:async args=>navigations.push(args),nextTick:fn=>queueMicrotask(fn),onWindowResize:()=>{},offWindowResize:()=>{}},useLoad:fn=>hooks.load=fn,useDidShow:fn=>hooks.show=fn,useDidHide:fn=>hooks.hide=fn},
  '../../services/workouts':{workoutApi:{history:async()=>{reads.push('history');return[data]},progress:async()=>{reads.push('progress');return{weeks:8,total_sessions:1,total_sets:3,total_reps:24,total_volume_kg:480,weekly:[]}},detail:async()=>{reads.push('detail');return data}}},
  '../../core/request':{errorMessage:e=>e.message},'../services/workouts':{workoutApi:{}},'../core/request':{errorMessage:e=>e.message}})
 return{p,hooks,reads,navigations}
}
test('history shows at most two trained exercises, count summary and independent proposal link without extra reads',async()=>{
 const {p,hooks,reads,navigations}=pageAt('../../src/pages/history/index.tsx');p.render();hooks.show();await p.flush()
 assert.equal(p.findAll('exercise-line').length,2);assert.match(p.text(),/已练 3 项 · 未训练 1 项 · 更多动作见详情/)
 assert.equal(p.find('history-adjustments'),undefined);assert.match(p.text(),/18:32/)
 await p.click('workout-detail-link');await p.click('adaptive-proposal-link')
 assert.deepEqual(navigations,[{url:'/pages/workout-detail/index?id=s%2F%26'},{url:'/pages/plan-proposal-detail/index?id=p%2F%26'}]);assert.deepEqual(reads,['history','progress'])
})
test('all-zero history does not show untrained exercises as completed; no-proposal advice is preserved',async()=>{
 const data=record();data.exercises=[ex('zero',0)];data.adaptive_adjustment_proposal=null
 const {p,hooks}=pageAt('../../src/pages/history/index.tsx',data);p.render();hooks.show();await p.flush()
 assert.equal(p.findAll('exercise-line').length,0);assert.match(p.text(),/已练 0 项 · 未训练 1 项/)
 assert.ok(p.find('history-adjustments'));assert.match(p.text(),/完整调整说明/)
})
test('details group zero-set actions, default-collapse trained cards and preserve notes/safety/actual records',async()=>{
 const data=record();data.exercises[0].safety_notice='安全提醒';data.notes='用户备注';data.feedback={feedback_notes:'真实反馈'}
 data.exercises[0].sets_data.push({set_number:2,reps:7,weight_kg:null})
 const {p,hooks,reads}=pageAt('../../src/pages/workout-detail/index.tsx',data);p.render();hooks.load({id:data.id});await p.flush()
 assert.equal(p.findAll('workout-disclosure').length,4);assert.equal(p.findAll('detail-exercise').length,3)
 assert.equal(p.findAll('untrained-item').length,1);assert.match(p.text(),/3组 × 8–12次/)
 assert.ok(p.findAll('workout-disclosure-body').every(node=>node.props.style.height==='0px'))
 for(const preserved of ['安全提醒','用户备注','真实反馈']) assert.ok(p.text().includes(preserved))
 assert.doesNotMatch(p.text(),/实际休息由计时开始|本组后实际休息|目标休息/)
 assert.match(p.text(),/组间歇17秒/);assert.match(p.text(),/组间歇未记录/);assert.match(p.text(),/未记录重量/)
 assert.deepEqual(reads,['detail']);hooks.hide();await p.flush();assert.ok(p.findAll('workout-disclosure-body').every(node=>node.props.style.transitionDuration==='0ms'))
 hooks.show();await p.flush();assert.deepEqual(reads,['detail'])
})
test('missing plan values remain unknown, not inferred from actual training',()=>{
 const helpers=interactionRuntime('../../src/core/workout-presentation.ts').exports
 assert.equal(helpers.plannedExerciseSummary({...ex('a'),target_sets:null,target_reps:null}),'—组 × —次')
 assert.equal(helpers.plannedExerciseSummary(ex('a',1)),'3组 × 8–12次')
 assert.equal(helpers.inlineSetRest({rest_end_reason:'workout_ended'}),'组间歇：训练结束，无下一组')
 assert.equal(helpers.inlineSetRest({actual_rest_seconds:0}),'组间歇0秒')
 assert.equal(helpers.workoutClock('2026-09-11T01:00:00Z'),'09:00')
 assert.equal(helpers.workoutClock('2026-09-11T01:00:00'),'09:00')
 assert.equal(helpers.workoutClock('invalid'),'')
})
