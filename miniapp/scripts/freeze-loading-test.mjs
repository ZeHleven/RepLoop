// Local isolated acceptance artifact, never a production release/upload command.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { cp, mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'

const repository = path.resolve('..')
assert.equal(path.basename(repository), 'loading-optimization')
const destination = path.resolve(repository, '../loading-optimization-test-20260911-r1')
assert.equal(path.dirname(destination), path.dirname(repository))
await mkdir(destination) // Deliberately refuse to overwrite an earlier acceptance artifact.
const target = path.join(destination, 'miniapp-dist')
await cp(path.resolve('dist'), target, {recursive:true})
const metadata = JSON.parse(await readFile(path.join(target,'build-info.json'),'utf8'))
assert.equal(metadata.source_dirty,true)
const config = {
  description:'加载优化隔离测试：仅预览，禁止上传正式版', miniprogramRoot:'./',
  projectname:'fitness-agent-loading-test-r1', appid:'wx740b4ecd32ee4627', compileType:'miniprogram', libVersion:'3.17.1',
  setting:{urlCheck:false,es6:true,postcss:false,minified:false,enhance:false,compileHotReLoad:false,compileWorklet:false,
    uglifyFileName:false,uploadWithSourceMap:true,packNpmManually:false,minifyWXSS:true,minifyWXML:true,swc:false,disableSWC:true},
  packOptions:{ignore:[],include:[]}
}
await writeFile(path.join(target,'project.config.json'),JSON.stringify(config,null,2)+'\n')
const manifest = {kind:'isolated-loading-test-not-for-upload', ...metadata, api_base_url:'http://192.168.0.7:8541/api/v1',
  expected_test_id:'agent-acceptance-20260910', backend_change:false, production_bundle_modified:false, files:[]}
async function scan (root, relative='') {
  for (const entry of await readdir(path.join(root,relative),{withFileTypes:true})) {
    const file = path.join(relative,entry.name)
    if (entry.isDirectory()) await scan(root,file)
    else {const bytes=await readFile(path.join(root,file)); manifest.files.push({path:file.replaceAll('\\','/'),bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')})}
  }
}
await scan(target)
manifest.files.sort((a,b)=>a.path.localeCompare(b.path))
manifest.total_bytes = manifest.files.reduce((n,file)=>n+file.bytes,0)
manifest.bundle_sha256 = createHash('sha256').update(JSON.stringify(manifest.files)).digest('hex')
await writeFile(path.join(destination,'manifest.json'),JSON.stringify(manifest,null,2)+'\n')
await cp(path.resolve('qa'),path.join(destination,'evidence'),{recursive:true,filter:source=>!source.startsWith(path.resolve('qa/h5'))})
console.log(JSON.stringify({directory:target,...metadata,bundle_sha256:manifest.bundle_sha256,total_bytes:manifest.total_bytes},null,2))
