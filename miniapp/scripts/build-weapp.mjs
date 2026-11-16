import { execFileSync, spawnSync } from 'node:child_process'
import { copyFile, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  createMiniappBuildMetadata,
  miniappBuildEnvironment
} from './miniapp-build-metadata.mjs'

const miniappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(miniappRoot, '..')
const packageJson = JSON.parse(await readFile(
  path.join(miniappRoot, 'package.json'),
  'utf8'
))
const projectVersion = (await readFile(
  path.join(repositoryRoot, 'backend', 'VERSION'), 'utf8'
)).trim()
if (!/^\d+\.\d+\.\d+$/u.test(projectVersion) || packageJson.version !== projectVersion) {
  throw new Error('miniapp/package.json must match backend/VERSION; run scripts/check_workspace.py --sync-version')
}
if (process.env.TARO_APP_BUILD_VERSION && process.env.TARO_APP_BUILD_VERSION !== projectVersion) {
  throw new Error('TARO_APP_BUILD_VERSION differs from backend/VERSION')
}

function gitOutput (...arguments_) {
  return execFileSync('git', arguments_, {
    cwd: repositoryRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore']
  }).trim()
}

const headCommit = gitOutput('rev-parse', '--verify', 'HEAD').toLowerCase()
if (
  process.env.GITHUB_SHA &&
  process.env.GITHUB_SHA.trim().toLowerCase() !== headCommit
) {
  throw new Error('GITHUB_SHA does not match the checked-out Git HEAD')
}

const metadata = createMiniappBuildMetadata({
  version: projectVersion,
  commit: headCommit,
  sourceDirty: gitOutput('status', '--porcelain').length > 0
})
const buildEnvironment = {
  ...process.env,
  ...miniappBuildEnvironment(metadata)
}
const taroCli = path.join(
  miniappRoot,
  'node_modules',
  '@tarojs',
  'cli',
  'bin',
  'taro'
)

console.log(
  `Building miniapp ${metadata.build_version} ` +
  `(${metadata.build_commit.slice(0, 12)}, dirty=${metadata.source_dirty})`
)
const build = spawnSync(
  process.execPath,
  [taroCli, 'build', '--type', 'weapp'],
  { cwd: miniappRoot, env: buildEnvironment, stdio: 'inherit' }
)
if (build.error) throw build.error
if (build.status !== 0) process.exit(build.status ?? 1)

await copyFile(path.join(miniappRoot, 'THIRD-PARTY-NOTICES.md'), path.join(miniappRoot, 'dist', 'THIRD-PARTY-NOTICES.txt'))

await writeFile(
  path.join(miniappRoot, 'dist', 'build-info.json'),
  `${JSON.stringify(metadata, null, 2)}\n`,
  'utf8'
)

const validation = spawnSync(
  process.execPath,
  [path.join(miniappRoot, 'scripts', 'validate-weapp-build.mjs')],
  { cwd: miniappRoot, env: buildEnvironment, stdio: 'inherit' }
)
if (validation.error) throw validation.error
if (validation.status !== 0) process.exit(validation.status ?? 1)
