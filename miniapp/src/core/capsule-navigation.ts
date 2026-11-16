export const capsuleTabs = [
  { label: '训练', path: '/pages/workouts/index' },
  { label: '饮食', path: '/pages/nutrition/index' },
  { label: 'Agent', path: '/pages/agent/index' },
  { label: '我的', path: '/pages/me/index' }
]
export const tabIndex = (route: string) => capsuleTabs.findIndex(tab => tab.path === '/' + route.replace(/^\//, '').split('?')[0])
export interface CapsuleState { selected: number; from: number; started: number; revision: number; animate: boolean; keyboard: boolean; error: string }
// Same easing as the accepted preview; used only to interrupt at the current position.
export function capsuleProgress (elapsed: number) {
  const x = Math.max(0, Math.min(1, elapsed / 300))
  let lo = 0, hi = 1
  const curve = (t: number, a: number, b: number) => 3 * (1-t) ** 2 * t * a + 3 * (1-t) * t*t*b + t**3
  for (let i = 0; i < 16; i++) { const t = (lo+hi)/2; if (curve(t,.22,.36) < x) lo=t; else hi=t }
  return curve((lo+hi)/2,.61,1)
}
export function createCapsuleNavigation (io: { switchTab: (index: number) => Promise<unknown>; current: () => number; hideKeyboard: () => void; now?: () => number }) {
  const now = io.now || Date.now
  let state: CapsuleState = { selected: 0, from: 0, started: 0, revision: 0, animate: false, keyboard: false, error: '' }
  let confirmed = 0, desired: number | null = null, flight = false, paused = false
  let timer: ReturnType<typeof setTimeout> | null = null
  const listeners = new Set<(value: CapsuleState) => void>()
  const emit = (patch: Partial<CapsuleState>) => { state = { ...state, ...patch }; listeners.forEach(fn => fn(state)) }
  const clear = () => { if (timer !== null) clearTimeout(timer); timer = null }
  const snap = (index: number) => { clear(); emit({selected:index,from:index,animate:false,error:'',keyboard:false}) }
  const move = (index: number) => {
    const progress = state.animate ? capsuleProgress(now()-state.started) : 1
    const from = state.from + (state.selected-state.from)*progress
    clear(); emit({selected:index,from,started:now(),revision:state.revision+1,animate:true,error:'',keyboard:false})
    timer = setTimeout(() => {timer=null;emit({animate:false,from:state.selected})},340)
  }
  async function pump () {
    if (flight || paused || desired === null) return
    const target = desired
    if (target === confirmed) {desired=null;return}
    flight=true
    try {
      await io.switchTab(target)
      const actual = io.current(); confirmed=actual >= 0 ? actual : target
      if (desired===target) {desired=null;if(confirmed!==target)snap(confirmed)}
    } catch {
      const actual=io.current();if(actual>=0)confirmed=actual
      if(desired===target){desired=null;snap(confirmed);emit({error:'切换未完成，请再点一次目标页面。'})}
    } finally {flight=false;if(!paused && desired!==null)void pump()}
  }
  return {
    snapshot: () => state,
    subscribe: (fn: (value: CapsuleState) => void) => {listeners.add(fn);fn(state);return () => {listeners.delete(fn)}},
    select (index: number) {
      if(paused || index<0 || index>=capsuleTabs.length || !Number.isInteger(index) || index===state.selected)return
      try {io.hideKeyboard()}catch{}
      desired=index;move(index);void pump()
    },
    show (index: number) {
      if(index<0 || index>=capsuleTabs.length)return
      confirmed=index;paused=false
      // onShow also covers login redirects, deep links and programmatic switchTab.
      if(!flight && desired===null){if(state.selected!==index)snap(index);else emit({keyboard:false})}
      else emit({keyboard:false})
    },
    keyboard: (height: number) => emit({keyboard:Number.isFinite(height)&&height>0}),
    pause () { paused=true;desired=null;clear();emit({animate:false,keyboard:false}) },
    dispose () { paused=true;desired=null;clear();listeners.clear() }
  }
}
