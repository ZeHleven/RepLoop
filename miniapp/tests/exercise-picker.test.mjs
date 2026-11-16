import assert from 'node:assert/strict'
import test from 'node:test'
import { interactionRuntime, manualClock } from './helpers/interaction-runtime.mjs'

const options = [
  { exercise_id:'bench', exercise_name:'卧推', body_parts:['胸部'], search_aliases:['bench press'], equipment:['barbell'], category:'力量', difficulty:'中级' },
  { exercise_id:'row', exercise_name:'器械高位划船', body_parts:['背部'], search_aliases:['high row','高位拉背'], equipment:['high_row_machine'], category:'力量', difficulty:'初级' },
  { exercise_id:'fly', exercise_name:'蝴蝶机反向飞鸟', body_parts:['肩部'], search_aliases:['reverse pec deck'], equipment:['machine'], category:'力量', difficulty:'初级' }
]
function fixture (extra = {}) {
  const clock = manualClock(), selected = [], visibility = [], hooks = {}, listeners = new Set()
  const page = interactionRuntime('../../src/components/ExercisePicker.tsx', {
    '@tarojs/taro': { __esModule:true, useDidHide:fn => { hooks.hide=fn }, default:{
      getWindowInfo:() => ({ windowHeight:720 }), nextTick:fn => queueMicrotask(fn), hideKeyboard:async () => {},
      onKeyboardHeightChange:fn => listeners.add(fn), offKeyboardHeightChange:fn => listeners.delete(fn)
    } }
  }, clock.globals)
  const props = { options, selectedIds:['bench'], currentId:'bench', dayLabel:'周二', label:'替换', onSelect:option => selected.push(option), onOpenChange:open => visibility.push(open), ...extra }
  page.render(props)
  return { ...page, props, clock, selected, visibility, hooks, listeners }
}
async function open (page) { await page.click('open-exercise-picker'); await page.flush(); page.clock.advance(30); await page.flush() }
async function settle (page) { page.clock.advance(260); await page.flush() }

test('picker searches eligible names and aliases, switches body filters, and handles empty/clear', async () => {
  const p=fixture(); await open(p)
  assert.equal(p.findAll('library-body-part').length,8)
  p.input('library-search-input','HIGH ROW'); await p.flush(); p.clock.advance(121); await p.flush()
  assert.equal(p.findAll('library-result').length,1); assert.match(p.text(),/器械高位划船/)
  await p.click('library-body-part',1); await p.flush(); assert.ok(p.find('library-empty'))
  await p.click('reset-exercise-search'); await p.flush(); assert.equal(p.findAll('library-result').length,3)
  p.input('library-search-input','高位拉背'); await p.flush(); p.clock.advance(121); await p.flush()
  assert.equal(p.findAll('library-result').length,1)
  assert.equal(p.selected.length,0); p.unmount(); assert.equal(p.clock.count(),0)
})

test('same-day duplicates are disabled and even repeated stale clicks select only once', async () => {
  const p=fixture(); await open(p)
  assert.equal(p.findAll('library-result')[0].props.disabled,true)
  await p.click('library-result',0); assert.equal(p.selected.length,0)
  const click=p.findAll('library-result')[1].props.onClick
  click(); click(); await p.flush(); await settle(p)
  assert.equal(p.selected.length,1); assert.equal(p.selected[0].exercise_id,'row')
  assert.equal(p.visibility.at(-1),false); p.unmount()
})

test('cancel/background retains search, cleans keyboard/animation, and writes nothing', async () => {
  const p=fixture(); await open(p)
  p.input('library-search-input','飞鸟'); await p.flush(); p.clock.advance(121); await p.flush()
  for (const fn of p.listeners) fn({height:300})
  await p.flush(); assert.equal(p.find('library-picker-panel').props.style.bottom,'300px')
  await p.click('close-exercise-picker'); await settle(p); await open(p)
  assert.equal(p.find('library-search-input').props.value,'飞鸟')
  const stale=p.find('library-result').props.onClick
  p.hooks.hide(); stale(); await p.flush(); await settle(p)
  assert.equal(p.selected.length,0); assert.equal(p.visibility.at(-1),false); assert.equal(p.listeners.size,0)
  p.unmount(); assert.equal(p.clock.count(),0)
})

test('lost eligibility and disabled state cannot select a stale result', async () => {
  const p=fixture(); await open(p); const stale=p.findAll('library-result')[1].props.onClick
  p.render({...p.props, options:[options[0]]}); stale(); assert.equal(p.selected.length,0)
  p.render({...p.props,disabled:true}); await p.flush()
  assert.equal(p.find('exercise-sheet-layer').props.style.display,'none')
  p.unmount()
})

test('old response metadata remains searchable under All without inventing body parts', () => {
  const p=fixture(), {filterExerciseOptions}=p.loadSource('../../src/core/exercise-search.ts')
  const legacy=[{exercise_id:'old',exercise_name:'旧动作'}]
  assert.equal(filterExerciseOptions(legacy,'全部','旧动作').length,1)
  assert.equal(filterExerciseOptions(legacy,'背部','').length,0)
  p.unmount()
})

for (const target of ['plan-builder','plan-editor']) {
  test(`${target}: real picker add/replace/delete keeps prescriptions and only saves on explicit action`, async () => {
    const clock=manualClock(), hooks={}, writes=[]
    const original={exercise_id:'bench',exercise_name:'卧推',category:'力量',day_of_week:2,sets:4,reps:'6',rest_seconds:120,order_index:0,item_key:'planned:bench',recommended_weight_kg:20}
    const preview={name:'计划',goal:'strength',duration_weeks:4,days_per_week:1,session_duration_min:45,exercises:[original],exercise_options:options,rationale:[],safety_notes:[]}
    const p=interactionRuntime(`../../src/pages/${target}/index.tsx`,{
      '../../components/CustomExerciseEntry':{__esModule:true,default:()=>null},
      '../../core/request':{errorMessage:e=>e.message},
      '../../services/profile':{profileApi:{get:async()=>({onboarding_completed:true,training_days_per_week:1,session_duration_min:45})}},
      '../../services/workouts':{workoutApi:{previewPersonalizedPlan:async()=>preview,confirmPersonalizedPlan:async body=>{writes.push(body);throw new Error('测试保存失败')}}},
      '../../services/plan-management':{planManagementApi:{editContext:async()=>({base_plan:{...preview,training_days:[2]},base_plan_fingerprint:'a'.repeat(64),exercise_options:options,proposals_enabled:true}),createAdjustment:async(_id,_fp,body)=>{writes.push(body);throw new Error('测试保存失败')}}},
      '@tarojs/taro':{__esModule:true,useLoad:fn=>{hooks.load=fn},useDidHide:()=>{},useDidShow:()=>{},default:{
        getWindowInfo:()=>({windowHeight:720}),hideKeyboard:async()=>{},enableAlertBeforeUnload:async()=>{},disableAlertBeforeUnload:async()=>{}
      }}
    },clock.globals)
    p.render(); hooks.load({id:'plan'}); await p.flush()
    const expand=async(index)=>{await p.click('open-exercise-picker',index);await p.flush();clock.advance(30);await p.flush()}
    await expand(0); await p.click('library-result',1); await p.flush(); clock.advance(260); await p.flush()
    assert.equal(writes.length,0); assert.match(p.text(),/器械高位划船/)
    await p.click(target==='plan-builder'?'save-button':'save-proposal'); await p.flush()
    assert.equal(writes.length,1); assert.deepEqual([writes[0].exercises[0].sets,writes[0].exercises[0].reps,writes[0].exercises[0].rest_seconds],[4,'6',120])
    assert.match(p.text(),/测试保存失败/)
    await p.click('catalog-delete');await p.flush()
    assert.ok(p.find('catalog-draft-empty'));assert.equal(p.findAll('day-card').length,1)
    await p.click(target==='plan-builder'?'save-button':'save-proposal');await p.flush();assert.equal(writes.length,1)
    await expand(0)
    // All actual buttons are scoped to the currently mounted add sheet after deletion.
    await p.click('library-result',2);await p.flush();clock.advance(260);await p.flush()
    assert.equal(p.findAll('exercise-editor').length,1);assert.match(p.text(),/蝴蝶机反向飞鸟/)
    await p.click(target==='plan-builder'?'save-button':'save-proposal');await p.flush()
    assert.equal(writes.length,2);assert.equal(writes[1].exercises[0].exercise_id,'fly')
    assert.equal(writes[1].exercises[0].day_of_week,2)
    p.unmount();assert.equal(clock.count(),0)
  })
}
