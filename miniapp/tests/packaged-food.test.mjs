import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime } from './helpers/page-runtime.mjs'
const m = runtime('../../src/core/packaged-food.ts').exports
const form=()=>({...m.emptyPackagedFood(),name:'酸奶',amount:'150',calories:'280',protein:'4',carbs:'8',fat:'2'})
test('per100 basis independent from portion, kJ converted without449',()=>{const r=m.packagedFoodValues(form());assert.equal(r.data.amount_g,100);assert.equal(r.amount,150);assert.equal(r.totals.protein,6);assert.ok(Math.abs(r.totals.calories-280/4.184*1.5)<1e-9)})
test('zero distinct from empty, invalid fields cannot be saved',()=>{for(const k of ['calories','protein','carbs','fat']){assert.ok(m.packagedFoodValues({...form(),[k]:'0'}));for(const v of ['','-1','Infinity','NaN'])assert.equal(m.packagedFoodValues({...form(),[k]:v}),null)}})
test('clearing and replacing grams never replaces label',()=>{const f=form();assert.equal(m.packagedFoodValues({...f,amount:''}),null);assert.equal(m.packagedFoodValues({...f,amount:'200'}).totals.carbs,16);assert.equal(f.carbs,'8')})
test('old150g basis edits show per100g and leave source unchanged',()=>{const food={name_zh:'旧食品',calories_per_100g:140,protein_g:6,carbs_g:20,fat_g:4,basis:{name:'旧食品',amount_g:150,calories:210,protein_g:9,carbs_g:30,fat_g:6}};const before=JSON.stringify(food);const f=m.editPackagedFood(food);assert.equal(f.calories,'140');assert.equal(f.amount,'150');assert.equal(m.packagedFoodValues(f).totals.calories,210);assert.equal(JSON.stringify(food),before)})
test('energy unit roundtrip and empty input',()=>{const f=form();const c=m.convertPackagedEnergy(f,'kcal');assert.ok(Math.abs(Number(c.calories)-280/4.184)<1e-8);assert.equal(m.convertPackagedEnergy(c,'kJ').calories,'280');assert.equal(m.convertPackagedEnergy({...f,calories:''},'kcal').calories,'')})
