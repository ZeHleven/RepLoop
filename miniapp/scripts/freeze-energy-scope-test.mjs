// Builds no services and refuses to overwrite previous accepted artifacts.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
const repository=path.resolve('..')
assert.equal(path.basename(repository),'session-energy-scope')
const destination=path.resolve(repository,'../session-energy-scope-test-20260912-r1')
assert.equal(path.dirname(destination),path.dirname(repository))
const meta=JSON.parse(await readFile('dist/build-info.json','utf8'))
assert.equal(meta.source_dirty,true)
assert.equal(meta.build_commit,'f40b057d0c69f86ad89c28f08ca7cc2b3890f793')
const api='http://192.168.1.2:8541/api/v1'
const files=[]
async function scan(root,relative='') {
 for(const entry of await readdir(path.join(root,relative),{withFileTypes:true})){
  const name=path.join(relative,entry.name)
  if(entry.isDirectory())await scan(root,name)
  else {const bytes=await readFile(path.join(root,name));files.push({path:name.replaceAll('\\','/'),bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')})}
 }
}
// Sanity-check the compiled transport before creating a distributable directory.
let scripts=''
async function js(root){for(const entry of await readdir(root,{withFileTypes:true})){const file=path.join(root,entry.name);if(entry.isDirectory())await js(file);else if(file.endsWith('.js'))scripts+=await readFile(file,'utf8')}}
await js('dist');assert.ok(scripts.includes(api));assert.ok(!scripts.includes('reploop-prod-'));assert.ok(!scripts.includes('run.tcloudbase.com'))
await mkdir(destination)
const target=path.join(destination,'miniapp-dist');await cp('dist',target,{recursive:true})
await writeFile(path.join(target,'project.config.json'),JSON.stringify({description:'估算分类交互隔离测试：仅预览，禁止上传',miniprogramRoot:'./',projectname:'fitness-agent-energy-scope-test-r1',appid:'wx740b4ecd32ee4627',compileType:'miniprogram',libVersion:'3.17.1',setting:{urlCheck:false,es6:true,postcss:false,minified:false,enhance:false,compileHotReLoad:false,compileWorklet:false,uglifyFileName:false,uploadWithSourceMap:true,packNpmManually:false,minifyWXSS:true,minifyWXML:true,swc:false,disableSWC:true},packOptions:{ignore:[],include:[]}},null,2)+'\n')
await scan(target);files.sort((a,b)=>a.path.localeCompare(b.path))
const manifest={kind:'isolated-energy-scope-test-not-for-upload',...meta,api_base_url:api,expected_test_id:'agent-acceptance-20260910',backend_change:false,production_bundle_modified:false,files,total_bytes:files.reduce((s,f)=>s+f.bytes,0),bundle_sha256:createHash('sha256').update(JSON.stringify(files)).digest('hex')}
await writeFile(path.join(destination,'manifest.json'),JSON.stringify(manifest,null,2)+'\n')
await cp('qa',path.join(destination,'evidence'),{recursive:true})
const patch=execFileSync('git',['diff','--binary'],{cwd:repository});await writeFile(path.join(destination,'tracked-source.patch'),patch)
await cp(path.join(repository,'docs/session-energy-scope-20260912.md'),path.join(destination,'README.md'))
console.log(JSON.stringify({directory:target,...meta,bytes:manifest.total_bytes,sha256:manifest.bundle_sha256},null,2))
