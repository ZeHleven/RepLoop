const BUILD_VERSION_PATTERN = /^\d+\.\d+\.\d+$/u
const BUILD_COMMIT_PATTERN = /^[0-9a-f]{40}$/u
const BUILD_METADATA_KEYS = new Set([
  'schema_version',
  'build_version',
  'build_commit',
  'source_dirty'
])

export function validateMiniappBuildMetadata (value) {
  const errors = []
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return ['build metadata must be an object']
  }
  if (
    Object.keys(value).length !== BUILD_METADATA_KEYS.size ||
    Object.keys(value).some(key => !BUILD_METADATA_KEYS.has(key))
  ) {
    errors.push('build metadata keys do not match schema 1.0')
  }
  if (value.schema_version !== '1.0') {
    errors.push('schema_version must be 1.0')
  }
  if (
    typeof value.build_version !== 'string' ||
    !BUILD_VERSION_PATTERN.test(value.build_version)
  ) {
    errors.push('build_version must be a semantic version')
  }
  if (
    typeof value.build_commit !== 'string' ||
    !BUILD_COMMIT_PATTERN.test(value.build_commit)
  ) {
    errors.push('build_commit must be a full lowercase Git SHA')
  }
  if (typeof value.source_dirty !== 'boolean') {
    errors.push('source_dirty must be boolean')
  }
  return errors
}

export function createMiniappBuildMetadata ({
  version,
  commit,
  sourceDirty
}) {
  const metadata = {
    schema_version: '1.0',
    build_version: String(version || '').trim(),
    build_commit: String(commit || '').trim().toLowerCase(),
    source_dirty: sourceDirty
  }
  const errors = validateMiniappBuildMetadata(metadata)
  if (errors.length > 0) {
    throw new Error(`Invalid miniapp build metadata: ${errors.join('; ')}`)
  }
  return metadata
}

export function miniappBuildEnvironment (metadata) {
  const errors = validateMiniappBuildMetadata(metadata)
  if (errors.length > 0) {
    throw new Error(`Invalid miniapp build metadata: ${errors.join('; ')}`)
  }
  return {
    TARO_APP_BUILD_VERSION: metadata.build_version,
    TARO_APP_BUILD_COMMIT: metadata.build_commit,
    TARO_APP_BUILD_SOURCE_DIRTY: String(metadata.source_dirty)
  }
}
