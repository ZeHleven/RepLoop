import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import test from 'node:test'

import ts from 'typescript'

import {
  createMiniappBuildMetadata,
  miniappBuildEnvironment,
  validateMiniappBuildMetadata
} from '../scripts/miniapp-build-metadata.mjs'
import { validateWeappArtifact } from '../scripts/weapp-build-contract.mjs'

function loadTypeScriptModule (relativePath, globals = {}) {
  const sourceUrl = new URL(relativePath, import.meta.url)
  const sourcePath = fileURLToPath(sourceUrl)
  const output = ts.transpileModule(readFileSync(sourcePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: sourcePath
  }).outputText
  const loaded = { exports: {} }
  const globalNames = Object.keys(globals)
  const evaluate = new Function('exports', 'module', ...globalNames, output)
  evaluate(
    loaded.exports,
    loaded,
    ...globalNames.map(name => globals[name])
  )
  return loaded.exports
}

const runtimeEnvironment = loadTypeScriptModule('../config/runtime-env.ts')
const buildInfo = loadTypeScriptModule('../src/core/build-info.ts', {
  __FITNESS_BUILD_VERSION__: '1.2.3',
  __FITNESS_BUILD_COMMIT__: 'a'.repeat(40),
  __FITNESS_BUILD_SOURCE_DIRTY__: false
})

const validBuildManifest = {
  schema_version: '1.0',
  build_version: '1.2.3',
  build_commit: 'a'.repeat(40),
  source_dirty: false
}

function artifact (overrides = {}) {
  return {
    javascriptFiles: [{ path: 'common.js', content: 'const ready = true' }],
    appConfig: { pages: ['pages/proposal-detail/index'] },
    projectConfig: {
      miniprogramRoot: './',
      setting: { compileHotReLoad: false }
    },
    buildManifest: validBuildManifest,
    ...overrides
  }
}

test('missing optional transport variables become string literals', () => {
  assert.deepEqual(runtimeEnvironment.projectRuntimeDefineConstants({}), {
    __FITNESS_API_BASE_URL__: '""',
    __FITNESS_CLOUD_ENV__: '""',
    __FITNESS_CLOUD_SERVICE__: '""',
    __FITNESS_BUILD_VERSION__: '"development"',
    __FITNESS_BUILD_COMMIT__: '"unknown"',
    __FITNESS_BUILD_SOURCE_DIRTY__: 'true'
  })
})

test('runtime transport values are trimmed and safely quoted', () => {
  assert.deepEqual(runtimeEnvironment.projectRuntimeDefineConstants({
    TARO_APP_API_BASE_URL: ' https://api.example.test/api/v1/ ',
    TARO_APP_CLOUD_ENV: ' env-test ',
    TARO_APP_CLOUD_SERVICE: ' service-"quoted" ',
    TARO_APP_BUILD_VERSION: ' 1.2.3 ',
    TARO_APP_BUILD_COMMIT: ` ${'b'.repeat(40)} `,
    TARO_APP_BUILD_SOURCE_DIRTY: 'false'
  }), {
    __FITNESS_API_BASE_URL__: '"https://api.example.test/api/v1/"',
    __FITNESS_CLOUD_ENV__: '"env-test"',
    __FITNESS_CLOUD_SERVICE__: '"service-\\"quoted\\""',
    __FITNESS_BUILD_VERSION__: '"1.2.3"',
    __FITNESS_BUILD_COMMIT__: `"${'b'.repeat(40)}"`,
    __FITNESS_BUILD_SOURCE_DIRTY__: 'false'
  })
})

test('build metadata is strict and projects one canonical build environment', () => {
  const metadata = createMiniappBuildMetadata({
    version: '1.2.3',
    commit: 'A'.repeat(40),
    sourceDirty: false
  })
  assert.deepEqual(metadata, validBuildManifest)
  assert.deepEqual(validateMiniappBuildMetadata(metadata), [])
  assert.deepEqual(miniappBuildEnvironment(metadata), {
    TARO_APP_BUILD_VERSION: '1.2.3',
    TARO_APP_BUILD_COMMIT: 'a'.repeat(40),
    TARO_APP_BUILD_SOURCE_DIRTY: 'false'
  })
})

test('invalid or ambiguous build identities are rejected', () => {
  for (const candidate of [
    null,
    { ...validBuildManifest, build_version: 'latest' },
    { ...validBuildManifest, build_version: '1.2.3-beta' },
    { ...validBuildManifest, build_commit: 'abc1234' },
    { ...validBuildManifest, source_dirty: 'false' },
    { ...validBuildManifest, unexpected: true }
  ]) {
    assert.notDeepEqual(validateMiniappBuildMetadata(candidate), [])
  }
})

test('runtime build label exposes version, short commit, and dirty state', () => {
  assert.equal(buildInfo.miniappBuildLabel(), 'v1.2.3 · aaaaaaaaaaaa')
  assert.equal(buildInfo.miniappBuildLabel({
    ...validBuildManifest,
    source_dirty: true
  }), 'v1.2.3 · aaaaaaaaaaaa · dirty')
})

test('build identity is logged and visible before and after authentication', () => {
  const appSource = readFileSync(new URL('../src/app.ts', import.meta.url), 'utf8')
  const loginSource = readFileSync(
    new URL('../src/pages/login/index.tsx', import.meta.url),
    'utf8'
  )
  const agentSource = readFileSync(
    new URL('../src/pages/agent/index.tsx', import.meta.url),
    'utf8'
  )
  assert.match(appSource, /logMiniappBuildDiagnostic\(\)/u)
  assert.match(loginSource, /miniappBuildLabel\(\)/u)
  assert.match(agentSource, /miniappBuildLabel\(\)/u)
})

test('experience upload binds its version and release gate rejects dirty builds', () => {
  const uploadSource = readFileSync(
    new URL('../../scripts/upload_experience.ps1', import.meta.url),
    'utf8'
  )
  const preflightSource = readFileSync(
    new URL('../../scripts/release_preflight.ps1', import.meta.url),
    'utf8'
  )
  assert.match(uploadSource, /-MiniappVersion \$Version/u)
  assert.match(preflightSource, /build_version -ne \$MiniappVersion/u)
  assert.match(preflightSource, /source_dirty -ne \$false/u)
  assert.match(preflightSource, /build_commit -ne \$headCommit/u)
})

test('clean WeChat artifact satisfies the build contract', () => {
  assert.deepEqual(validateWeappArtifact(artifact()), [])
})

test('runtime process environment references fail the build contract', () => {
  const errors = validateWeappArtifact(artifact({
    javascriptFiles: [{
      path: 'pages/login/index.js',
      content: 'const env = process.env.TARO_APP_CLOUD_ENV'
    }]
  }))
  assert.deepEqual(errors.map(error => error.code), [
    'runtime_environment_reference'
  ])
})

test('route, hot reload, and project root violations fail independently', () => {
  const errors = validateWeappArtifact(artifact({
    appConfig: { pages: ['pages/index/index'] },
    projectConfig: {
      miniprogramRoot: 'dist/',
      setting: { compileHotReLoad: true }
    }
  }))
  assert.deepEqual(errors.map(error => error.code), [
    'required_page_missing',
    'hot_reload_enabled',
    'invalid_miniprogram_root'
  ])
})

test('project config is optional for source-only CI builds', () => {
  assert.deepEqual(validateWeappArtifact(artifact({ projectConfig: null })), [])
})

test('missing or malformed build manifest fails the artifact gate', () => {
  for (const buildManifest of [
    null,
    { ...validBuildManifest, build_commit: 'not-a-commit' }
  ]) {
    assert.deepEqual(
      validateWeappArtifact(artifact({ buildManifest })).map(error => error.code),
      ['build_manifest_invalid']
    )
  }
})

test('preview syntax gate rejects unsupported syntax with a file and location', () => {
  for (const content of [
    'const code = payload?.detail?.code',
    'const count = value ?? 0',
    'state.value ??= 1',
    'class Item { value = 1 }',
    'const malformed = ;'
  ]) {
    const errors = validateWeappArtifact(artifact({
      javascriptFiles: [{ path: 'common.js', content }]
    }))
    assert.deepEqual(errors.map(error => error.code), ['unsupported_javascript_syntax'])
    assert.match(errors[0].message, /common\.js:1:\d+/u)
  }
})

test('syntax gate parses code, not optional-chain text inside comments or strings', () => {
  assert.deepEqual(validateWeappArtifact(artifact({
    javascriptFiles: [{
      path: 'pages/nutrition/index.js',
      content: '/* a?.b */ const text = "value ?? fallback"; const re = /\\?\\./; const out = { ...{ value: 1 } }'
    }]
  })), [])
})

test('syntax gate checks page chunks as well as common.js', () => {
  const errors = validateWeappArtifact(artifact({
    javascriptFiles: [
      { path: 'common.js', content: 'const ok = true' },
      { path: 'pages/nutrition/index.js', content: 'const value = input?.value' }
    ]
  }))
  assert.equal(errors.length, 1)
  assert.match(errors[0].message, /pages\/nutrition\/index\.js:1:\d+/u)
})

test('actual WeChat Babel config lowers modern syntax without changing nullish or call semantics', () => {
  const source = `function exercise(input) {
    let reads = 0;
    const get = () => { reads++; return input; };
    const selected = get()?.nested?.value ?? 17;
    const context = input?.read?.();
    const kept = { value: 0 };
    kept.value ??= 99;
    return { selected, context, reads, kept: kept.value };
  }`
  // A fresh process uses the real build config and target, not a test-only preset.
  const output = execFileSync(process.execPath, ['-e', `
    const babel = require('@babel/core');
    const fs = require('node:fs');
    const output = babel.transformSync(fs.readFileSync(0, 'utf8'), {
      filename: 'src/preview-syntax-fixture.ts',
      configFile: require('node:path').resolve('babel.config.js'),
      babelrc: false
    }).code;
    process.stdout.write(output);
  `], {
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    env: { ...process.env, TARO_ENV: 'weapp', TARO_PLATFORM: 'mini', NODE_ENV: 'production' },
    input: source,
    encoding: 'utf8'
  })
  assert.deepEqual(validateWeappArtifact(artifact({
    javascriptFiles: [{ path: 'fixture.js', content: output }]
  })), [])
  assert.doesNotMatch(output, /\?\.|\?\?/u)
  const exercise = new Function(`${output}; return exercise;`)()
  assert.deepEqual(exercise(null), { selected: 17, context: undefined, reads: 1, kept: 0 })
  assert.deepEqual(exercise({ nested: { value: 0 } }), { selected: 0, context: undefined, reads: 1, kept: 0 })
  assert.deepEqual(exercise({ nested: { value: 5 }, read () { return this.nested.value } }), {
    selected: 5, context: 5, reads: 1, kept: 0
  })
})
