import { useEffect, useRef, useState } from 'react'
import { capsuleNavigation } from '../../core/capsule-platform'
import { trainingTimeLabel } from '../../core/exercise-energy'
import { Button, Input, Picker, ScrollView, Text, View } from '@tarojs/components'
import { emptyPackagedFood, packagedFoodValues, editPackagedFood, convertPackagedEnergy, foodBrowseCategories, foodPageSize } from '../../core/packaged-food'
import EdgeScrollView from '../../components/EdgeScrollView'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { peekCached, readCached, readCacheDay } from '../../core/read-cache'
import LoadingFeedback from '../../components/LoadingFeedback'
import { errorMessage } from '../../core/request'
import { changeMealAmount, changeMealNutrition, existingMealDraft, foodMealDraft, mealDraftCandidate, nutrientKeys, parseMealNumber } from '../../core/meal-draft'
import type { MealDraftItem, NutrientKey } from '../../core/meal-draft'
import { nutritionApi } from '../../services/nutrition'
import { profileApi } from '../../services/profile'
import { energyText, balanceLabel, macroPercent, macroEnergyMismatch, nutritionDate } from '../../core/nutrition-energy'
import type { DailyActivityLevel, DailyNutritionSummary, Food, MealItemInput, MealLog } from '../../types/api'
import './index.scss'

const mealTypes: MealLog['meal_type'][] = ['早餐', '午餐', '晚餐', '加餐']
const emptyCustom = emptyPackagedFood
type EditorAction = { kind: 'new' | 'close' } | { kind: 'edit', meal: MealLog }
type CustomAction = { kind: 'clear' } | { kind: 'edit', food: Food }

export default function NutritionPage () {
  const [scrollTarget, setScrollTarget] = useState('')
  const scrollRequest = useRef(0)
  const scrollToSection = (selector: string) => {
    const request = ++scrollRequest.current
    setScrollTarget('')
    try {
      Taro.nextTick(() => {
        if (request === scrollRequest.current) setScrollTarget(selector.replace(/^\./, ''))
      })
    } catch { /* Positioning is optional; never discard or save the meal draft. */ }
  }
  const scrollToMealEditor = () => scrollToSection('.meal-editor')
  const [today, setToday] = useState<DailyNutritionSummary | null>(() => peekCached<DailyNutritionSummary>('nutrition-today') || null)
  const [history, setHistory] = useState<DailyNutritionSummary[]>(() => peekCached<DailyNutritionSummary[]>('nutrition-history') || [])
  const [visible, setVisible] = useState(true)
  const shownDay = useRef(readCacheDay())
  const [foods, setFoods] = useState<Food[]>([])
  const [search, setSearch] = useState('')
  const [browseCategory, setBrowseCategory] = useState('')
  const [foodHasMore, setFoodHasMore] = useState(false)
  const [foodOffset, setFoodOffset] = useState(0)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const resumeFoodSearch = useRef(false)
  const [portion, setPortion] = useState('100')
  const [mealType, setMealType] = useState<MealLog['meal_type']>('早餐')
  const [loggedAt, setLoggedAt] = useState(localDate())
  const [editingMealId, setEditingMealId] = useState('')
  const [items, setItems] = useState<MealDraftItem[]>([])
  const [custom, setCustom] = useState(emptyCustom)
  const [editorOpen, setEditorOpen] = useState(false)
  const [addedFoodRevision, setAddedFoodRevision] = useState(0)
  const editorScrollGeneration = useRef(0)
  const [customOpen, setCustomOpen] = useState(false)
  const [customEditing, setCustomEditing] = useState<Food | null>(null)
  const [customAction, setCustomAction] = useState<CustomAction | null>(null)
  const [deleteFoodTarget, setDeleteFoodTarget] = useState<Food | null>(null)
  const [libraryScope, setLibraryScope] = useState<'all' | 'mine'>('all')
  const customRequest = useRef<{ signature: string, id: string, uncertain?: boolean } | null>(null)
  const [energyOpen, setEnergyOpen] = useState(false)
  const [activitySaving, setActivitySaving] = useState(false)
  const activityLock = useRef(false)
  const [activityError, setActivityError] = useState('')
  const [discardAction, setDiscardAction] = useState<EditorAction | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<MealLog | null>(null)
  const [todayLoading, setTodayLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [foodLoading, setFoodLoading] = useState(false)
  const [todayError, setTodayError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [foodError, setFoodError] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const savingLock = useRef(false)
  const itemSequence = useRef(0)
  const todayGeneration = useRef(0)
  const historyGeneration = useRef(0)
  const searchGeneration = useRef(0)
  const foodLoaded = useRef(false)
  const editorSeed = useRef('')
  const signature = (draft: MealDraftItem[], date: string, type: MealLog['meal_type'], extra = emptyCustom()) => JSON.stringify([draft, date, type, extra])
  const dirty = editorOpen && signature(items, loggedAt, mealType, custom) !== editorSeed.current
  const nextKey = () => `meal-item-${++itemSequence.current}`

  const refreshToday = async (force = true) => {
    const generation = ++todayGeneration.current
    if (shownDay.current !== readCacheDay()) { shownDay.current = readCacheDay(); setToday(null) }
    setTodayLoading(true); setTodayError('')
    try {
      const value = await readCached('nutrition-today', nutritionApi.today, force)
      if (generation === todayGeneration.current) setToday(value)
    } catch (e) {
      if (generation === todayGeneration.current) setTodayError(errorMessage(e, '今日记录加载失败'))
    } finally { if (generation === todayGeneration.current) setTodayLoading(false) }
  }
  const refreshHistory = async (force = true) => {
    const generation = ++historyGeneration.current
    setHistoryLoading(true); setHistoryError('')
    try {
      const value = await readCached('nutrition-history', nutritionApi.history, force)
      if (generation === historyGeneration.current) setHistory(value)
    } catch (e) {
      if (generation === historyGeneration.current) setHistoryError(errorMessage(e, '历史记录加载失败'))
    } finally { if (generation === historyGeneration.current) setHistoryLoading(false) }
  }
  const load = (force = true) => Promise.all([refreshToday(force), refreshHistory(force)])
  useDidShow(() => {
    capsuleNavigation.show(1)
    setVisible(true)
    if (shownDay.current !== readCacheDay()) {
      // Never label yesterday's energy as today's; leave the meal draft untouched.
      shownDay.current = readCacheDay(); setToday(null)
    }
    void load(false)
    if (editorOpen && resumeFoodSearch.current) {
      resumeFoodSearch.current = false
      void searchFoods(search.trim())
    }
  })
  useDidHide(() => {
    resumeFoodSearch.current = foodLoading || searchTimer.current !== null
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchGeneration.current++; setFoodLoading(false)
    scrollRequest.current++
    setVisible(false); todayGeneration.current++; historyGeneration.current++; editorScrollGeneration.current++
  })
  useEffect(() => () => {
    if (searchTimer.current) clearTimeout(searchTimer.current)
    scrollRequest.current++
    todayGeneration.current++; historyGeneration.current++; searchGeneration.current++
  }, [])

  const searchFoods = async (query: string, scope = libraryScope, category = browseCategory, append = false) => {
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = null
    const generation = ++searchGeneration.current
    const offset = append ? foodOffset : 0
    setFoodLoading(true); setFoodError('')
    if (!append) { setFoods([]); setFoodOffset(0); setFoodHasMore(false); foodLoaded.current = false }
    try {
      const value = await nutritionApi.foods(query, foodPageSize + 1, scope, scope === 'mine' ? '' : category, offset)
      if (generation !== searchGeneration.current) return
      const page = value.slice(0, foodPageSize)
      setFoods(current => append ? [...current, ...page.filter(row => !current.some(old => old.id === row.id && old.source === row.source))] : page)
      setFoodOffset(offset + page.length); setFoodHasMore(value.length > foodPageSize); foodLoaded.current = true
    } catch (e) {
      if (generation === searchGeneration.current) setFoodError(errorMessage(e, '食品搜索失败，请重试'))
    } finally { if (generation === searchGeneration.current) setFoodLoading(false) }
  }
  const findFoods = () => searchFoods(search.trim())
  const changeSearch = (query: string) => {
    setSearch(query); searchGeneration.current++; setFoodLoading(false); setFoodError(''); setFoods([]); setFoodHasMore(false); foodLoaded.current = false
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => { void searchFoods(query.trim()) }, 250)
  }

  const applyEditorAction = (action: EditorAction, saved = false) => {
    if (savingLock.current && !saved) return
    const meal = action.kind === 'edit' ? action.meal : null
    const draft = meal ? meal.items.map(item => existingMealDraft(item, nextKey())) : []
    const date = meal?.logged_at || localDate()
    const type = meal?.meal_type || '早餐'
    setEditingMealId(meal?.id || ''); setLoggedAt(date); setMealType(type)
    setItems(draft); setCustom(emptyCustom()); setCustomOpen(false); setCustomEditing(null); setCustomAction(null); setDeleteFoodTarget(null); customRequest.current = null
    setEditorOpen(action.kind !== 'close'); setDiscardAction(null); setError('')
    editorSeed.current = signature(draft, date, type)
    if (action.kind !== 'close' && !foodLoaded.current && !foodLoading) void searchFoods(search.trim())
  }
  useEffect(() => {
    const generation = ++editorScrollGeneration.current
    if (editorOpen) {
      try {
        Taro.nextTick(() => {
          if (generation === editorScrollGeneration.current) void scrollToMealEditor()
        })
      } catch {
        // A native rendering/scroll failure must not discard the meal draft.
      }
    }
    return () => { editorScrollGeneration.current++; scrollRequest.current++ }
  }, [editorOpen, editingMealId, addedFoodRevision])
  useEffect(() => {
    const selector = discardAction ? '.discard-prompt' : deleteTarget ? '.delete-prompt' : error ? '.editor-error' : ''
    if (selector) scrollToSection(selector)
  }, [discardAction, deleteTarget, error])
  const requestEditorAction = (action: EditorAction) => {
    if (savingLock.current) return
    if (action.kind === 'edit' && editorOpen && action.meal.id === editingMealId) {
      scrollToMealEditor()
      return
    }
    if (dirty) setDiscardAction(action)
    else applyEditorAction(action)
  }
  const editMeal = (meal: MealLog) => requestEditorAction({ kind: 'edit', meal })

  const addFood = (food: Food) => {
    if (savingLock.current) return
    const grams = parseMealNumber(portion, 10000, false)
    if (grams === null) { setError('请输入大于 0 且不超过 10000 克的有效份量'); return }
    if (items.length >= 30) { setError('每餐最多添加 30 项食物'); return }
    const draft = foodMealDraft(food, grams, nextKey())
    setItems(current => current.length < 30 ? [...current, draft] : current); setError('')
    setAddedFoodRevision(current => current + 1)
  }
  const addCustom = async () => {
    if (savingLock.current) return
    const parsed = packagedFoodValues(custom)
    if (!custom.name.trim() || custom.name.trim().length > 100 || !parsed) {
      setError('请补全名称、每100g营养和有效份量。空白不能当作 0，请核对热量单位'); return
    }
    if (!customEditing && items.length >= 30) { setError('每餐最多添加 30 项食物'); return }
    const { data, amount } = parsed
    const signature = JSON.stringify(data)
    // Reconcile the original request even if the user edited the form after a
    // response was lost. Never guess from a matching name or blindly create again.
    if (!customEditing && customRequest.current?.uncertain) {
      savingLock.current = true; setSaving(true); setError('')
      try {
        const prior = await nutritionApi.foodCreationResult(customRequest.current.id)
        if (customRequest.current.signature === signature) {
          setItems(current => [...current, foodMealDraft(prior, amount, nextKey())])
          setAddedFoodRevision(current => current + 1)
          setCustom(emptyCustom()); customRequest.current = null
          setLibraryScope('mine'); setSearch(''); await searchFoods('', 'mine')
        } else {
          customRequest.current = null
          setError('上一次食品已保存到我的食品。当前新输入已保留，请确认后再次保存；未自动加入旧食品。')
          await searchFoods(search.trim())
        }
        return
      } catch (e) {
        const status = (e as { statusCode?: number })?.statusCode
        if (status !== 404) { setError(errorMessage(e, '暂时无法核对上次保存结果，输入仍保留')); return }
        if (customRequest.current) customRequest.current.uncertain = false
      } finally { savingLock.current = false; setSaving(false) }
    }
    if (!customRequest.current || customRequest.current.signature !== signature) {
      customRequest.current = { signature, id: `food-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}` }
    }
    savingLock.current = true; setSaving(true); setError('')
    try {
      const food = customEditing
        ? await nutritionApi.updateFood(customEditing.id, { ...data, version: customEditing.version! })
        : await nutritionApi.createFood({ ...data, client_request_id: customRequest.current.id })
      if (!customEditing) {
        setItems(current => [...current, foodMealDraft(food, amount, nextKey())])
        setAddedFoodRevision(current => current + 1)
      }
      setCustom(emptyCustom()); setCustomEditing(null); customRequest.current = null
      setLibraryScope('mine'); setSearch(''); await searchFoods('', 'mine')
      await Taro.showToast({ title: customEditing ? '食品已修改，旧餐不变' : '已存入食品库并加入草稿', icon: 'none' }).catch(() => {})
    } catch (e) {
      const status = (e as { statusCode?: number })?.statusCode
      if (!customEditing && customRequest.current && (!status || status >= 500 || status === 408)) customRequest.current.uncertain = true
      setError(errorMessage(e, '食品保存结果未确认；输入已保留，重试时先核对结果'))
    }
    finally { savingLock.current = false; setSaving(false) }
  }
  const applyCustomAction = (action: CustomAction) => {
    if (savingLock.current) return
    const food = action.kind === 'edit' ? action.food : null
    setCustom(food ? editPackagedFood(food) : emptyCustom())
    setCustomEditing(food); setCustomAction(null); setCustomOpen(true); customRequest.current = null
    scrollToSection('.custom-form')
  }
  const requestCustomAction = (action: CustomAction) => {
    if (savingLock.current) return
    if (JSON.stringify(custom) !== JSON.stringify(emptyCustom())) setCustomAction(action)
    else applyCustomAction(action)
  }
  const deleteFood = async () => {
    if (!deleteFoodTarget || savingLock.current) return
    savingLock.current = true; setSaving(true); setError('')
    try {
      await nutritionApi.deleteFood(deleteFoodTarget.id, deleteFoodTarget.version!)
      setDeleteFoodTarget(null); await searchFoods(search.trim())
      await Taro.showToast({ title: '食品已删除，历史餐次不变', icon: 'none' }).catch(() => {})
    } catch (e) { setError(errorMessage(e, '删除结果尚未确认，请刷新食品库核对')) }
    finally { savingLock.current = false; setSaving(false) }
  }
  const changeActivity = async (level: DailyActivityLevel) => {
    if (activityLock.current) return
    activityLock.current = true; setActivitySaving(true); setActivityError('')
    try { await profileApi.update({ daily_activity_level: level }); await refreshToday() }
    catch (e) { setActivityError(errorMessage(e, '活动水平保存未确认，请重试')) }
    finally { activityLock.current = false; setActivitySaving(false) }
  }
  const updateItemAmount = (key: string, raw: string) => {
    if (!savingLock.current) setItems(current => current.map(item => item.key === key ? changeMealAmount(item, raw) : item))
  }
  const updateCustomNutrition = (key: string, field: NutrientKey, raw: string) => {
    if (!savingLock.current) setItems(current => current.map(item => item.key === key ? changeMealNutrition(item, field, raw) : item))
  }
  const candidates = items.map(mealDraftCandidate)
  const customPreview = packagedFoodValues(custom)
  const draftValid = items.length > 0 && items.length <= 30 && candidates.every(Boolean)
  const totals = candidates.reduce((sum, item) => ({
    calories: sum.calories + (item?.calories || 0), protein: sum.protein + (item?.protein_g || 0),
    carbs: sum.carbs + (item?.carbs_g || 0), fat: sum.fat + (item?.fat_g || 0)
  }), { calories: 0, protein: 0, carbs: 0, fat: 0 })

  const saveMeal = async () => {
    if (savingLock.current) return
    if (JSON.stringify(custom) !== JSON.stringify(emptyCustom())) {
      setCustomOpen(true)
      setError('还有未添加的自定义食物。请先添加到本餐，或清空自定义表单后再保存。')
      return
    }
    if (!draftValid) { setError('请先补全每项食品的有效名称、克数和营养数据'); return }
    savingLock.current = true; setSaving(true); setError('')
    try {
      const candidate = { logged_at: loggedAt, meal_type: mealType, items: candidates as MealItemInput[] }
      if (editingMealId) await nutritionApi.updateMeal(editingMealId, candidate)
      else await nutritionApi.logMeal(candidate)
      const updated = Boolean(editingMealId)
      applyEditorAction({ kind: 'close' }, true)
      await Taro.showToast({ title: updated ? '修改已保存' : '餐次已记录', icon: 'success' }).catch(() => {})
      await load()
    } catch (e) {
      const status = (e as { statusCode?: number } | null)?.statusCode
      setError(status && status >= 400 && status < 500 && status !== 408 && status !== 429
        ? errorMessage(e, '保存未成功，请检查填写内容；草稿已保留')
        : '暂时无法确认保存结果，草稿已保留。请先刷新餐次记录核对，避免重复新增。')
    } finally { savingLock.current = false; setSaving(false) }
  }
  const deleteMeal = async () => {
    if (!deleteTarget || savingLock.current) return
    const target = deleteTarget
    savingLock.current = true; setSaving(true); setError('')
    try {
      await nutritionApi.deleteMeal(target.id)
      if (editingMealId === target.id) applyEditorAction({ kind: 'close' }, true)
      setDeleteTarget(null); await load()
    } catch (e) { setError(errorMessage(e, '删除结果尚未确认，请刷新记录核对')) }
    finally { savingLock.current = false; setSaving(false) }
  }
  const mealRows = (meals: MealLog[]) => meals.map(meal => (
    <View className='history-meal' key={meal.id}>
      <View className='history-meal-heading'><Text className='meal-name'>{meal.meal_type}</Text>
        <View className='meal-actions'>
          <Button className='edit-meal' size='mini' disabled={saving} onClick={() => editMeal(meal)}>编辑</Button>
          <Button className='delete-meal' size='mini' disabled={saving} onClick={() => setDeleteTarget(meal)}>删除</Button>
        </View>
      </View>
      <Text className='meal-items'>{meal.items.map(item => `${item.food_name} ${item.amount_g}g`).join(' · ')}</Text>
    </View>
  ))

  return (
    <EdgeScrollView className='nutrition-scroll' contentSelector='.nutrition-page' visible={visible} scrollY scrollWithAnimation scrollIntoView={scrollTarget} scrollIntoViewAlignment='start'>
    <View className='page nutrition-page'>
      <Text className='nutrition-eyebrow'>今天吃得怎么样</Text><Text className='nutrition-title'>饮食记录</Text>
      {todayError && <View className='error-banner'>{todayError}<Button className='secondary-button today-retry' onClick={() => refreshToday()}>重试今日记录</Button></View>}
      <LoadingFeedback loading={todayLoading} hasContent={Boolean(today)} visible={visible} text='正在加载今日饮食…' refreshingText='正在更新今日饮食…' />
      {today && <View className='card daily-card'>
        <View className='daily-overview'>
          <View className='daily-intake'><Text className='daily-label'>今日摄入</Text><Text className='daily-calories'>{formatNumber(today.total_calories)}<Text className='daily-unit'>kcal</Text></Text></View>
          <View className='energy-column'>
            <EnergyMetric label='基础代谢' value={today.energy_estimate?.bmr_kcal} />
            <EnergyMetric label={today.energy_estimate?.status === 'partial' ? '已估算消耗' : '全天消耗'} value={today.energy_estimate?.total_kcal} />
            <EnergyMetric label={balanceLabel(today.energy_estimate?.balance_kcal)} value={today.energy_estimate?.balance_kcal} />
          </View>
        </View>
        {today.energy_estimate?.status === 'partial' && <Text className='energy-warning'>部分估算 · 有训练未计入</Text>}
        {today.energy_estimate?.reasons.map(reason => <Text className='energy-reason' key={reason}>{reason}</Text>)}
        <View className='macro-row'><Macro label='蛋白质' value={today.total_protein_g} percent={macroPercent(today.total_protein_g, 4, today.total_calories)} /><Macro label='碳水' value={today.total_carbs_g} percent={macroPercent(today.total_carbs_g, 4, today.total_calories)} /><Macro label='脂肪' value={today.total_fat_g} percent={macroPercent(today.total_fat_g, 9, today.total_calories)} /></View>
        {macroEnergyMismatch(today.total_protein_g, today.total_carbs_g, today.total_fat_g, today.total_calories) && <Text className='energy-warning'>营养素换算热量与记录总热量差异较大，请核对食品数据；原始记录不会被自动修改。</Text>}
        <Text className='energy-note'>按已记录饮食与估算消耗计算，仅供参考。</Text>
        <Button className='energy-toggle' onClick={() => setEnergyOpen(!energyOpen)}>{energyOpen ? '收起计算依据' : '查看计算依据与活动设置'}</Button>
        {energyOpen && <View className='energy-details'>
          <Text className='energy-detail-title'>日常活动（不含专门安排的训练）</Text>
          <View className='activity-options'>{(['sedentary', 'walking', 'physical_work'] as const).map((level, index) => <Button key={level} className={`activity-choice activity-${level} ${today.energy_estimate?.activity_level === level ? 'is-selected' : ''}`} disabled={activitySaving} onClick={() => changeActivity(level)}>{['久坐', '经常走动', '体力劳动'][index]}</Button>)}</View>
          {activityError && <Text className='field-error'>{activityError}</Text>}
          {today.energy_estimate?.activity_defaulted && <Text>尚未设置，暂按久坐估算。</Text>}
          <Text>基础代谢：10 × 体重kg ＋ 6.25 × 身高cm − 5 × 年龄，男性加 5，女性减 161（Mifflin–St Jeor 静息能量估算）。</Text>
          {today.energy_estimate?.profile_inputs && <Text>本次资料：{today.energy_estimate.profile_inputs.gender === 'male' ? '男性' : '女性'} · {today.energy_estimate.profile_inputs.age} 岁 · {today.energy_estimate.profile_inputs.height_cm} cm · {today.energy_estimate.profile_inputs.weight_kg} kg（{today.energy_estimate.profile_inputs.weight_source === 'profile' ? '当前档案' : '最近体重记录'}）</Text>}
          <Button className='energy-profile' onClick={() => Taro.navigateTo({ url: '/pages/profile-edit/index' })}>查看或补充个人资料</Button>
          <Text>日常消耗按个人基础代谢乘 1.2／1.4／1.6 粗估，不是所有人使用同一热量。训练另计净增加消耗，扣除对应时段的日常基线，避免重复计算。</Text>
          {today.energy_estimate?.baseline_kcal != null && <Text>本次日常基线：{energyText(today.energy_estimate.baseline_kcal)}（基础代谢 × {today.energy_estimate.activity_factor}）。训练总消耗约为 MET × 体重kg × 小时，再减去同一时段的日常基线；日常基线按 24 小时均匀分配，这是粗估假设。</Text>}
          <Text>训练使用自动开始、结束时间；正常休息包含在内，单段休息超过 10 分钟的部分不计入估算。自动计时无法完全识别所有中途停留，计划和进行中的训练不提前计入。</Text>
          {today.energy_estimate?.workouts?.map(workout => <View className='energy-workout' key={workout.session_id}>
            <Text>{trainingTimeLabel(workout.started_at, workout.trained_at)} · {workout.plan_name || '本次训练'}</Text>
            <Text>{workout.reason || `${Math.round(workout.effective_minutes || 0)} 分钟 · ${workout.met} MET · 净增加 ${energyText(workout.net_kcal)}`}</Text>
            {workout.reason && !workout.unestimated_exercises?.length && !!workout.performed_exercises?.length && <Text>实际训练动作：{workout.performed_exercises.map(row => row.exercise_name).join('、')}</Text>}
            {workout.unestimated_exercises?.map(row => <Text key={row.session_exercise_id}>{row.exercise_name}：{row.reason_code === 'classification_missing' ? '尚未补充分类' : '暂不支持此类消耗估算'}</Text>)}
            {workout.reason && <Button className='secondary-button energy-workout-detail' onClick={() => Taro.navigateTo({ url: `/pages/workout-detail/index?id=${encodeURIComponent(workout.session_id)}` })}>{workout.reason_code === 'classification_missing' ? '补充分类' : '查看训练'}</Button>}
          </View>)}
          <Text>缺口／盈余按已记录摄入对比估算全天消耗，不代表当天饮食已全部记录。供能占比采用蛋白质／碳水每克 4 kcal、脂肪每克 9 kcal；食品数据口径及舍入可能使合计不等于 100%。</Text>
          <Text>依据：Mifflin–St Jeor（1990）、2024 Compendium of Physical Activities；活动系数与中断阈值是本版粗估假设，不构成医疗处方。</Text>
        </View>}
      </View>}
      <View className='section-heading-row'><Text className='history-heading'>今天已记录</Text><Button className='refresh-meals' size='mini' onClick={() => load()}>刷新记录</Button></View>
      {today && <View className='card today-meals'>{today.meals.length ? mealRows(today.meals) : <Text className='empty-copy'>今天还没有记录。可以记录一餐，也可以请 Agent 帮你制定方案。</Text>}</View>}
      <Button className='primary-button start-meal' disabled={saving} onClick={() => requestEditorAction({ kind: 'new' })}>记录一餐</Button>
      {discardAction && <View id='discard-prompt' className='card inline-confirm discard-prompt'>
        <Text>当前有未保存的修改。放弃后才能继续，已保存的餐次不会受影响。</Text>
        <View className='confirm-actions'><Button className='secondary-button keep-draft' onClick={() => setDiscardAction(null)}>继续编辑</Button><Button className='secondary-button discard-draft' onClick={() => applyEditorAction(discardAction)}>放弃修改并继续</Button></View>
      </View>}
      {deleteTarget && <View id='delete-prompt' className='card inline-confirm delete-prompt'>
        <Text>删除{deleteTarget.logged_at}的{deleteTarget.meal_type}及全部食物明细？此操作无法撤销。若正在编辑此餐，草稿也会清除。</Text>
        <View className='confirm-actions'><Button className='secondary-button' disabled={saving} onClick={() => setDeleteTarget(null)}>保留记录</Button><Button className='secondary-button confirm-delete' disabled={saving} onClick={deleteMeal}>确认删除</Button></View>
      </View>}
      {error && <View id='editor-error' className='error-banner editor-error'>{error}</View>}
      {editorOpen && <View id='meal-editor' className='card meal-editor'>
        <View className='editor-row'><Text className='editor-label'>{editingMealId ? '编辑餐次' : '记录餐次'}</Text>
          <Picker disabled={saving} mode='selector' range={mealTypes} value={mealTypes.indexOf(mealType)} onChange={e => setMealType(mealTypes[Number(e.detail.value)])}><View className='meal-picker'>{mealType} ⌄</View></Picker>
        </View>
        <View className='editor-row date-row'><Text className='date-label'>记录日期</Text>
          <Picker disabled={saving} mode='date' value={loggedAt} start={earliestEditableDate()} end={localDate()} onChange={e => setLoggedAt(e.detail.value)}><View className='meal-picker'>{loggedAt} ⌄</View></Picker>
        </View>
        <View className='selected-items'>
          <Text className='selected-title'>本次餐次（{items.length}）</Text>
          {!items.length && <Text className='empty-copy'>从下方食品库添加食品，或展开自定义食物。</Text>}
          {items.map(item => <View className='selected-row' key={item.key}>
            <View className='selected-main'>
              {item.food_id ? <Text className='selected-name'>{item.food_name}</Text> : <Input disabled={saving} className='selected-name-input' value={item.food_name} maxlength={100} onInput={e => setItems(current => current.map(row => row.key === item.key ? { ...row, food_name: e.detail.value, custom_food_id: undefined, custom_food_version: undefined } : row))} />}
              <View className='selected-amount'><Input disabled={saving} className='selected-amount-input' type='digit' value={item.amountText} onInput={e => updateItemAmount(item.key, e.detail.value)} /><Text>克</Text></View>
              {!mealDraftCandidate(item) && <Text className='field-error'>请补全有效名称、克数和营养数据；空白不能当作 0。</Text>}
              {item.food_id && mealDraftCandidate(item) && <Text className='food-meta'>预估 {item.nutrients.calories} kcal · 保存时按食品库重新计算</Text>}
              {!item.food_id && <>
                <Text className='nutrition-basis-note'>以下营养为当前份量的总量；修改克数会按比例换算。修改名称或营养仅影响本餐，不修改食品库。</Text>
                <View className='selected-custom-grid'>{nutrientKeys.map((field, i) => <SmallInput key={field} label={['kcal', '蛋白g', '碳水g', '脂肪g'][i]} disabled={saving || parseMealNumber(item.amountText, 10000, false) === null} value={item.nutrients[field]} onInput={raw => updateCustomNutrition(item.key, field, raw)} />)}</View>
              </>}
            </View>
            <Button className='remove-item' size='mini' disabled={saving} onClick={() => setItems(current => current.filter(row => row.key !== item.key))}>删除</Button>
          </View>)}
          {!!items.length && <View className='draft-summary'>{draftValid ? <><Text>整餐预估 {formatNumber(totals.calories)} kcal</Text><Text>蛋白 {formatNumber(totals.protein)}g · 碳水 {formatNumber(totals.carbs)}g · 脂肪 {formatNumber(totals.fat)}g</Text></> : <Text>请补全上方字段后查看整餐营养并保存。</Text>}</View>}
          <View className={`meal-editor-actions${editingMealId ? ' is-editing' : ''}`}>
            <Button className='primary-button save-meal' loading={saving} disabled={saving || !draftValid} onClick={saveMeal}>{editingMealId ? '保存修改' : `保存${mealType}`}</Button>
            <Button className='secondary-button cancel-edit' disabled={saving} onClick={() => requestEditorAction({ kind: 'close' })}>{editingMealId ? '取消编辑' : '收起编辑'}</Button>
          </View>
        </View>
        <Text className='custom-title'>添加食品</Text>
        <View className='library-tabs'>{(['all', 'mine'] as const).map(scope => <Button key={scope} className={`library-tab library-${scope} ${libraryScope === scope ? 'is-selected' : ''}`} disabled={saving} onClick={() => { setLibraryScope(scope); void searchFoods(search.trim(), scope) }}>{scope === 'all' ? '全部食品' : '我的食品'}</Button>)}</View>
        <View className='search-row'><Input disabled={saving} className='search-input' maxlength={50} value={search} placeholder='搜索名称或别名' onInput={e => changeSearch(e.detail.value)} onConfirm={findFoods} />{!!search && <Button className='search-clear' disabled={saving} onClick={() => changeSearch('')}>清空</Button>}<Button className='search-button' size='mini' disabled={saving} onClick={findFoods}>搜索</Button></View>
        {libraryScope === 'all' && <ScrollView scrollX className='food-categories'><View className='food-category-strip'>{['', ...foodBrowseCategories].map(category => <Button key={category || 'all'} className={`food-category ${browseCategory === category ? 'is-selected' : ''}`} disabled={saving} onClick={() => { setBrowseCategory(category); void searchFoods(search.trim(), libraryScope, category) }}>{category || '全部分类'}</Button>)}</View></ScrollView>}
        <View className='portion-row'><Text>添加份量</Text><Input disabled={saving} className='portion-input' type='digit' value={portion} onInput={e => setPortion(e.detail.value)} /><Text>克</Text></View>
        {foodLoading && <LoadingFeedback text='正在找寻食品' loading visible={visible} hasContent={foods.length > 0} />}
        {foodError && <View className='error-banner'>{foodError}<Button className='secondary-button food-retry' onClick={() => searchFoods(search.trim(), libraryScope, browseCategory, foodHasMore && foods.length > 0)}>重试搜索</Button></View>}
        {!foodLoading && !foodError && !foods.length && <Text className='empty-copy'>{foodLoaded.current ? '未找到匹配食品，可以换个名称或添加自定义食物。' : '输入名称后点击搜索。'}</Text>}
        <View className='food-results'>{foods.map(food => <View className='food-row' key={`${food.source || 'standard'}-${food.id}`}><View className='food-copy'><Text className='food-name'>{food.name_zh}{food.source === 'custom' ? ' · 我的食品' : ''}</Text><Text className='food-meta'>{formatNumber(food.calories_per_100g)} kcal / 100g · 蛋白 {formatNumber(food.protein_g)}g</Text>{food.source === 'custom' && <View className='library-actions'><Button className='library-edit' disabled={saving} onClick={() => requestCustomAction({ kind: 'edit', food })}>编辑</Button><Button className='library-delete' disabled={saving} onClick={() => setDeleteFoodTarget(food)}>删除</Button></View>}</View><Button className='food-add' size='mini' disabled={saving} onClick={() => addFood(food)}>添加</Button></View>)}</View>
        {foodHasMore && <Button className='secondary-button food-more' disabled={saving || foodLoading} onClick={() => searchFoods(search.trim(), libraryScope, browseCategory, true)}>加载更多</Button>}
        <Text className='nutrition-basis-note'>按可食部分称重，留意名称中的生熟状态。包装食品可按标签录入到我的食品。</Text>
        {deleteFoodTarget && <View className='inline-confirm food-delete-prompt'><Text>从我的食品库删除“{deleteFoodTarget.name_zh}”？旧餐次不变，当前草稿不会被自动修改。</Text><View className='confirm-actions'><Button disabled={saving} onClick={() => setDeleteFoodTarget(null)}>保留</Button><Button className='confirm-food-delete' disabled={saving} onClick={deleteFood}>确认删除食品</Button></View></View>}
        {customAction && <View className='inline-confirm custom-discard-prompt'><Text>自定义食品表单有未保存内容，放弃后再继续？餐次草稿会保留。</Text><View className='confirm-actions'><Button className='keep-custom' onClick={() => setCustomAction(null)}>继续填写</Button><Button className='discard-custom' onClick={() => applyCustomAction(customAction)}>放弃表单并继续</Button></View></View>}
        <Button className='secondary-button toggle-custom' disabled={saving} onClick={() => setCustomOpen(!customOpen)}>{customOpen ? '收起自定义食物' : '食品库没有？添加自定义食物'}</Button>
        {customOpen && <View id='custom-form' className='custom-form'>
          <Text className='custom-title'>{customEditing ? `编辑食品：${customEditing.name_zh}` : '添加我的食品'}</Text>
          <Text className='nutrition-basis-note'>仅自己可见</Text>
          <Input disabled={saving} className='custom-input wide' value={custom.name} maxlength={100} placeholder='食物名称' onInput={e => setCustom(current => ({ ...current, name: e.detail.value }))} />
          <Text className='custom-title'>每100g营养</Text><Text className='nutrition-basis-note'>照着包装营养表填写，不是整包总量。</Text>
          <View className='packaged-grid'>{(['calories', 'carbs', 'protein', 'fat'] as const).map((field, i) => <View className='packaged-field' key={field}><Text>{['热量', '碳水', '蛋白质', '脂肪'][i]}</Text><View className='packaged-input-unit'><Input className={`small-input custom-${field}`} disabled={saving} type='digit' value={custom[field]} placeholder='未填写' onInput={e => setCustom(current => ({ ...current, [field]: e.detail.value }))} />{field === 'calories' ? <Picker className='custom-energy-unit' disabled={saving} mode='selector' range={['kJ', 'kcal']} value={custom.unit === 'kJ' ? 0 : 1} onChange={e => setCustom(current => convertPackagedEnergy(current, Number(e.detail.value) === 0 ? 'kJ' : 'kcal'))}><Text>{custom.unit}⌄</Text></Picker> : <Text>g</Text>}</View></View>)}</View>
          <View className='packaged-portion'><Text>{customEditing ? '换算预览份量' : '本次实际份量'}</Text><View className='packaged-input-unit'><Input disabled={saving} className='small-input custom-amount' type='digit' value={custom.amount} onInput={e => setCustom(current => ({ ...current, amount: e.detail.value }))} /><Text>g</Text></View></View>
          <View className='packaged-totals'><View><Text>本次份量营养</Text><Text className='packaged-total-energy'>{customPreview ? formatNumber(customPreview.totals.calories) : '—'} kcal</Text></View><Text>碳水 {customPreview ? formatNumber(customPreview.totals.carbs) : '—'}g · 蛋白质 {customPreview ? formatNumber(customPreview.totals.protein) : '—'}g · 脂肪 {customPreview ? formatNumber(customPreview.totals.fat) : '—'}g</Text></View>
          <Text className='nutrition-basis-note'>请使用每100g标签；每100mL不能直接照填。{customEditing ? '修改只影响以后从库中选择的食品，不改变当前餐次。' : '先加入餐次草稿，保存餐次后才算记录。'}</Text>
          {customPreview?.warnings.map(warning => <Text key={warning} className='energy-warning'>{warning}</Text>)}
          <Button className='primary-button custom-add' disabled={saving} onClick={addCustom}>{customEditing ? '保存食品修改（不改变当前餐次）' : '保存到食品库并添加'}</Button>
          <Button className='secondary-button clear-custom' disabled={saving} onClick={() => requestCustomAction({ kind: 'clear' })}>{customEditing ? '取消食品编辑' : '清空未添加的自定义食物'}</Button>
        </View>}
      </View>}
      <Text className='history-heading'>过去 29 天</Text>
      {historyLoading && !history.length && <Text className='loading-state'>正在加载历史记录…</Text>}
      {historyError && <View className='error-banner'>{historyError}<Button className='secondary-button history-retry' onClick={() => refreshHistory()}>重试历史记录</Button></View>}
      {!historyLoading && !historyError && !history.some(day => day.date < localDate()) && <View className='card empty-state'>过去 29 天还没有饮食记录。</View>}
      {history.filter(day => day.date < localDate()).map(day => <View className='card history-day' key={day.date}><View className='history-day-heading'><Text className='history-date'>{day.date}</Text><Text className='history-total'>{formatNumber(day.total_calories)} kcal</Text></View>{mealRows(day.meals)}</View>)}
    </View>
    </EdgeScrollView>
  )
}

function EnergyMetric ({ label, value }: { label: string, value?: number | null }) {
  return <View className='energy-metric'><Text className='energy-label'>{label}</Text><Text className='energy-value'>{energyText(value)}</Text></View>
}
function Macro ({ label, value, percent }: { label: string, value: number, percent: string }) {
  return <View className='macro'><Text className='macro-value'>{formatNumber(value)}g</Text><Text className='macro-label'>{label}</Text><Text className='macro-percent'>供能 {percent}</Text></View>
}
function SmallInput ({ label, value, onInput, disabled }: { label: string, value: string, onInput: (value: string) => void, disabled?: boolean }) {
  return <View className='small-input-wrap'><Input disabled={disabled} className='small-input' type='digit' value={value} onInput={e => onInput(e.detail.value)} /><Text>{label}</Text></View>
}
function formatNumber (value: number): string { return Math.round(value * 10) / 10 + '' }
function localDate (): string {
  return nutritionDate()
}
function earliestEditableDate (): string {
  return nutritionDate(29)
}
