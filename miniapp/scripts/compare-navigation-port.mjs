import {chromium} from 'file:///C:/Users/12521/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
import {readFile} from 'node:fs/promises'
const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true})
try {
 const page=await browser.newPage({viewport:{width:850,height:1080},deviceScaleFactor:1})
 const files=['../../scroll-shadow-preview-20260912-r1/qa/compact/训练-compact-nav.png','qa/navigation/workouts-compact-nav.png','../../scroll-shadow-preview-20260912-r1/qa/compact/训练-compact.png','qa/navigation/workouts-fade-middle.png']
 const images=await Promise.all(files.map(async p=>(await readFile(p)).toString('base64')))
 await page.setContent(`<body style="margin:24px;background:#f5f7f2;color:#173226;font:14px system-ui"><h2>Accepted compact navigation / Taro port</h2><div style="display:flex;gap:24px"><section><p>Preview (CSS pixel sizing)</p><img src="data:image/png;base64,${images[0]}"></section><section><p>Taro (750-unit responsive sizing)</p><img src="data:image/png;base64,${images[1]}"></section></div><p>Below: different fixture content and scroll states; compare edge treatment only, not card layout or device chrome.</p><div style="display:flex;gap:16px"><img width="393" src="data:image/png;base64,${images[2]}"><img width="393" src="data:image/png;base64,${images[3]}"></div></body>`)
 await page.screenshot({path:'qa/navigation/port-comparison.png',fullPage:true})
} finally {await browser.close()}
