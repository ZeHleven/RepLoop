import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import {readFile,readdir,mkdir,cp,writeFile} from 'node:fs/promises'
import path from 'node:path'
const repository=path.resolve('..'),destination=path.resolve(repository,'../capsule-navigation-test-20260912-r4')
assert.equal(path.basename(repository),'capsule-navigation');assert.equal(path.dirname(destination),path.dirname(repository))
const meta=JSON.parse(await readFile('dist/build-info.json','utf8'))
assert.equal(meta.build_commit,'fad8b8661d8022bd512755c5d0975fb759c8e043');assert.equal(meta.source_dirty,true)
const app=JSON.parse(await readFile('dist/app.json','utf8'));assert.equal(app.tabBar.custom,true);assert.equal(app.tabBar.list.length,4)
for(const ext of ['js','json','wxml'])assert.ok((await readFile(`dist/custom-tab-bar/index.${ext}`)).length)
assert.equal(JSON.parse(await readFile('dist/custom-tab-bar/index.json','utf8')).component,true)
const api='http://192.168.1.21:8541/api/v1'
let scripts='';async function js(dir){for(const e of await readdir(dir,{withFileTypes:true})){const p=path.join(dir,e.name);if(e.isDirectory())await js(p);else if(p.endsWith('.js'))scripts+=await readFile(p,'utf8')}}
await js('dist');assert.ok(scripts.includes(api));assert.ok(!scripts.includes('reploop-prod-'));assert.ok(!scripts.includes('run.tcloudbase.com'));assert.ok(scripts.includes('addGlobalClass'))
await mkdir(destination);const target=path.join(destination,'miniapp-dist');await cp('dist',target,{recursive:true})
await writeFile(path.join(target,'project.config.json'),JSON.stringify({description:'胶囊导航隔离测试 r4，紧凑比例与滚动渐隐，仅预览，禁止上传',miniprogramRoot:'./',projectname:'fitness-agent-capsule-navigation-test-r4',appid:'wx740b4ecd32ee4627',compileType:'miniprogram',libVersion:'3.17.1',setting:{urlCheck:false,es6:true,postcss:false,minified:false,enhance:false,compileHotReLoad:false,compileWorklet:false,uglifyFileName:false,uploadWithSourceMap:true,packNpmManually:false,minifyWXSS:true,minifyWXML:true,swc:false,disableSWC:true}},null,2)+'\n')
const files=[];async function scan(dir,rel=''){for(const e of await readdir(path.join(dir,rel),{withFileTypes:true})){const p=path.join(rel,e.name);if(e.isDirectory())await scan(dir,p);else {const b=await readFile(path.join(dir,p));files.push({path:p.replaceAll('\\','/'),bytes:b.length,sha256:createHash('sha256').update(b).digest('hex')})}}}
await scan(target);files.sort((a,b)=>a.path.localeCompare(b.path))
const manifest={kind:'isolated-navigation-not-for-upload',...meta,api_base_url:api,expected_test_id:'agent-acceptance-20260910',backend_change:false,files,total_bytes:files.reduce((n,f)=>n+f.bytes,0),bundle_sha256:createHash('sha256').update(JSON.stringify(files)).digest('hex')}
await writeFile(path.join(destination,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');await cp('qa',path.join(destination,'evidence'),{recursive:true});await cp('../docs/capsule-navigation-20260912.md',path.join(destination,'README.md'))
console.log(JSON.stringify({directory:target,...meta,total_bytes:manifest.total_bytes,sha256:manifest.bundle_sha256},null,2))
