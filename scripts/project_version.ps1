function Get-ProjectVersion {
    $root = Split-Path -Parent $PSScriptRoot
    $version = (Get-Content -LiteralPath (Join-Path $root 'backend/VERSION') -Raw).Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+$') {
        throw 'backend/VERSION must contain a semantic version.'
    }
    $package = Get-Content -LiteralPath (Join-Path $root 'miniapp/package.json') -Raw | ConvertFrom-Json
    if ($package.version -ne $version) {
        throw 'miniapp/package.json version differs from backend/VERSION. Run scripts/check_workspace.py --sync-version.'
    }
    return $version
}
