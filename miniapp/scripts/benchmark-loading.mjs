// Executes baseline and current production TSX handlers. Fixed fake I/O latency;
// these timings are not measurements of the production network/backend or a phone.
import { loadingPage } from '../tests/helpers/loading-fixtures.mjs'
import { writeFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { performance } from 'node:perf_hooks'
const baseline = process.argv[2]
if (!baseline) throw Error('Provide an isolated baseline miniapp source directory')
const delayMs = 200, results = []
const sleep = ms => new Promise(done => setTimeout(done,ms))
for (const name of ['workouts','nutrition','me','history','workout-detail']) {
  for (const version of ['baseline','optimized']) {
    const runs = []
    for (let i = 0; i < 3; i++) {
      let pending = 0
      const page = loadingPage(name, { sourceFile: version === 'baseline' ? pathToFileURL(resolve(baseline,'pages',name,'index.tsx')).href : undefined,
        transport: async () => { pending++; try { await sleep(delayMs) } finally { pending-- } }
      })
      async function enter () {
        const start = performance.now(), count = page.calls.length
        await page.show()
        while (pending) { await sleep(2); await page.flush() }
        await page.flush()
        return { ms: +(performance.now()-start).toFixed(1), requests: page.calls.length-count }
      }
      const first = await enter(); await page.hide(); const repeat = await enter()
      runs.push({ first, repeat }); page.unmount()
    }
    const median = field => [...runs.map(r=>r[field].ms)].sort((a,b)=>a-b)[1]
    results.push({ name, version, firstMs:median('first'), repeatMs:median('repeat'), firstRequests:runs[0].first.requests, repeatRequests:runs[0].repeat.requests, runs })
  }
}
await mkdir('qa',{recursive:true})
const report = { method:'Real TSX handlers, substituted lifecycle and fake HTTP boundary; 3 runs, median; warm return under 30 seconds; NOT native-device or production latency.', delayMs, results }
await writeFile('qa/loading-benchmark.json',JSON.stringify(report,null,2))
console.table(results.map(({runs,...row})=>row))
