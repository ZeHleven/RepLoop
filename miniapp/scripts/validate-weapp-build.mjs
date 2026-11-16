import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { validateWeappArtifact } from './weapp-build-contract.mjs'
import { validateNativeTabbar } from './native-tabbar-contract.mjs'

const miniappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const distRoot = path.join(miniappRoot, 'dist')

async function collectJavaScriptFiles (directory) {
  const files = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolutePath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...await collectJavaScriptFiles(absolutePath))
    } else if (entry.isFile() && entry.name.endsWith('.js')) {
      files.push({
        path: path.relative(distRoot, absolutePath).replaceAll('\\', '/'),
        content: await readFile(absolutePath, 'utf8')
      })
    }
  }
  return files
}

async function readJson (filePath, required) {
  try {
    return JSON.parse(await readFile(filePath, 'utf8'))
  } catch (error) {
    if (!required && error?.code === 'ENOENT') return null
    throw new Error(`Cannot read ${path.relative(miniappRoot, filePath)}: ${error.message}`)
  }
}

const javascriptFiles = await collectJavaScriptFiles(distRoot)
if (javascriptFiles.length === 0) {
  throw new Error('WeChat build contains no JavaScript files')
}

const errors = validateWeappArtifact({
  javascriptFiles,
  appConfig: await readJson(path.join(distRoot, 'app.json'), true),
  projectConfig: await readJson(path.join(distRoot, 'project.config.json'), false),
  buildManifest: await readJson(path.join(distRoot, 'build-info.json'), true)
})

errors.push(...validateNativeTabbar({ javascriptFiles, appConfig: await readJson(path.join(distRoot, 'app.json'), true) }))

if (errors.length > 0) {
  for (const error of errors) console.error(`[${error.code}] ${error.message}`)
  process.exitCode = 1
} else {
  console.log(`WeChat build contract passed (${javascriptFiles.length} JavaScript files checked).`)
}
