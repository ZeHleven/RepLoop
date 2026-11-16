const readRuntimeEnvironment = (
  environment: NodeJS.ProcessEnv,
  name: keyof NodeJS.ProcessEnv
): string => {
  const value = environment[name]
  return typeof value === 'string' ? value.trim() : ''
}

export function projectRuntimeDefineConstants (
  environment: NodeJS.ProcessEnv
): Record<string, string> {
  const sourceDirty = readRuntimeEnvironment(
    environment,
    'TARO_APP_BUILD_SOURCE_DIRTY'
  )
  return {
    __FITNESS_API_BASE_URL__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_API_BASE_URL')
    ),
    __FITNESS_CLOUD_ENV__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_CLOUD_ENV')
    ),
    __FITNESS_CLOUD_SERVICE__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_CLOUD_SERVICE')
    ),
    __FITNESS_BUILD_VERSION__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_BUILD_VERSION') ||
      'development'
    ),
    __FITNESS_BUILD_COMMIT__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_BUILD_COMMIT') ||
      'unknown'
    ),
    __FITNESS_BUILD_SOURCE_DIRTY__: JSON.stringify(sourceDirty !== 'false')
  }
}
