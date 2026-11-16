import { createRequire } from 'node:module'
import { validateMiniappBuildMetadata } from './miniapp-build-metadata.mjs'

// Use the parser from our pinned Webpack dependency, never a global installation.
const require = createRequire(import.meta.url)
const webpackRequire = createRequire(require.resolve('webpack/package.json'))
const { parse } = webpackRequire('acorn')
const WEAPP_ECMA_VERSION = 2019
const RUNTIME_ENVIRONMENT_PATTERN = /\bprocess\s*(?:\.\s*env|\[\s*['"]env['"]\s*\])/u
const REQUIRED_PAGES = ['pages/proposal-detail/index']

export function validateWeappArtifact ({
  javascriptFiles,
  appConfig,
  projectConfig = null,
  buildManifest
}) {
  const errors = []

  for (const file of javascriptFiles) {
    try {
      // Node's own parser accepts newer syntax that the preview compiler rejects.
      // Parse every final chunk so dependencies/minification cannot bypass Babel.
      parse(file.content, { ecmaVersion: WEAPP_ECMA_VERSION, sourceType: 'script' })
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error
      errors.push({
        code: 'unsupported_javascript_syntax',
        message: `${file.path}:${error.loc.line}:${error.loc.column} is not valid ES${WEAPP_ECMA_VERSION} preview JavaScript: ${error.message}`
      })
    }
    if (RUNTIME_ENVIRONMENT_PATTERN.test(file.content)) {
      errors.push({
        code: 'runtime_environment_reference',
        message: `${file.path} contains a runtime process.env reference`
      })
    }
  }

  const pages = Array.isArray(appConfig?.pages) ? appConfig.pages : []
  for (const page of REQUIRED_PAGES) {
    if (!pages.includes(page)) {
      errors.push({
        code: 'required_page_missing',
        message: `app.json is missing required page ${page}`
      })
    }
  }

  if (
    projectConfig &&
    projectConfig.setting?.compileHotReLoad !== false
  ) {
    errors.push({
      code: 'hot_reload_enabled',
      message: 'project.config.json must disable compileHotReLoad'
    })
  }

  if (projectConfig && projectConfig.miniprogramRoot !== './') {
    errors.push({
      code: 'invalid_miniprogram_root',
      message: 'dist/project.config.json must use ./ as miniprogramRoot'
    })
  }

  const metadataErrors = validateMiniappBuildMetadata(buildManifest)
  if (metadataErrors.length > 0) {
    errors.push({
      code: 'build_manifest_invalid',
      message: metadataErrors.join('; ')
    })
  }

  return errors
}
