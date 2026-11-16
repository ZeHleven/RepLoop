export interface MiniappBuildInfo {
  schema_version: '1.0'
  build_version: string
  build_commit: string
  source_dirty: boolean
}

export function projectMiniappBuildInfo (
  version: string,
  commit: string,
  sourceDirty: boolean
): MiniappBuildInfo {
  return {
    schema_version: '1.0',
    build_version: version,
    build_commit: commit,
    source_dirty: sourceDirty
  }
}

export const MINIAPP_BUILD_INFO = Object.freeze(projectMiniappBuildInfo(
  __FITNESS_BUILD_VERSION__,
  __FITNESS_BUILD_COMMIT__,
  __FITNESS_BUILD_SOURCE_DIRTY__
))

export function miniappBuildLabel (
  build: MiniappBuildInfo = MINIAPP_BUILD_INFO
): string {
  const commit = build.build_commit === 'unknown'
    ? 'unknown'
    : build.build_commit.slice(0, 12)
  return `v${build.build_version} · ${commit}${
    build.source_dirty ? ' · dirty' : ''
  }`
}

export function logMiniappBuildDiagnostic (): void {
  console.info(
    'miniapp_build_diagnostic',
    JSON.stringify(MINIAPP_BUILD_INFO)
  )
}
