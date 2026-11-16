import { execFileSync, spawnSync } from 'node:child_process'
import { copyFile, readFile, readdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createMiniappBuildMetadata, miniappBuildEnvironment } from './miniapp-build-metadata.mjs'

const miniappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const root = path.resolve(miniappRoot, '..')
const version = (await readFile(path.join(root, 'backend/VERSION'), 'utf8')).trim()
const pkg = JSON.parse(await readFile(path.join(miniappRoot, 'package.json'), 'utf8'))
if (pkg.version !== version || (process.env.TARO_APP_BUILD_VERSION && process.env.TARO_APP_BUILD_VERSION !== version)) {
  throw new Error('H5 version must match backend/VERSION; run scripts/check_workspace.py --sync-version')
}
const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
const head = git('rev-parse', 'HEAD')
if (process.env.GITHUB_SHA && process.env.GITHUB_SHA.toLowerCase() !== head) throw new Error('GITHUB_SHA differs from HEAD')
const metadata = createMiniappBuildMetadata({ version, commit: head, sourceDirty: Boolean(git('status', '--porcelain')) })
const environment = { ...process.env, ...miniappBuildEnvironment(metadata),
  TARO_ENV: 'h5', TARO_APP_DEMO_MODE: 'true', TARO_APP_API_BASE_URL: '/api/v1',
  TARO_APP_CLOUD_ENV: '', TARO_APP_CLOUD_SERVICE: '' }
const hashes = {}
async function fingerprint (relative) {
  const absolute = path.join(miniappRoot, relative)
  for (const entry of await readdir(absolute, { withFileTypes: true })) {
    const name = path.posix.join(relative, entry.name)
    if (entry.isDirectory()) await fingerprint(name)
    else hashes[name] = createHash('sha256').update(await readFile(path.join(miniappRoot, name))).digest('hex')
  }
}
for (const directory of ['src', 'config', 'types', 'scripts']) await fingerprint(directory)
for (const name of ['package.json', 'pnpm-lock.yaml', 'babel.config.js', 'tsconfig.json']) {
  hashes[name] = createHash('sha256').update(await readFile(path.join(miniappRoot, name))).digest('hex')
}
console.log(`Building demo H5 ${version} (${head.slice(0, 12)}, dirty=${metadata.source_dirty})`)
const build = spawnSync(process.execPath, [path.join(miniappRoot, 'node_modules/@tarojs/cli/bin/taro'), 'build', '--type', 'h5'],
  { cwd: miniappRoot, env: environment, stdio: 'inherit' })
if (build.error) throw build.error
if (build.status !== 0) process.exit(build.status ?? 1)
await copyFile(path.join(miniappRoot, 'THIRD-PARTY-NOTICES.md'), path.join(miniappRoot, 'dist-h5/THIRD-PARTY-NOTICES.txt'))
await writeFile(path.join(miniappRoot, 'dist-h5/build-info.json'), `${JSON.stringify(metadata, null, 2)}\n`)
await writeFile(path.join(miniappRoot, 'dist-h5/source-hashes.json'), `${JSON.stringify(hashes, null, 2)}\n`)
