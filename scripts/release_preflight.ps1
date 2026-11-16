[CmdletBinding()]
param(
    [switch]$SkipRemoteHealth,

    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$MiniappVersion
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_version.ps1')
$projectVersion = Get-ProjectVersion
if ($MiniappVersion -and $MiniappVersion -ne $projectVersion) {
    throw 'Requested preflight version differs from backend/VERSION.'
}
$MiniappVersion = $projectVersion
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendEnvPath = Join-Path $projectRoot '.env.production'
$miniappLocalEnvPath = Join-Path $projectRoot 'miniapp\.env.production.local'
$miniappProductionEnvPath = Join-Path $projectRoot 'miniapp\.env.production'
$miniappEnvPath = if (Test-Path -LiteralPath $miniappLocalEnvPath) {
    $miniappLocalEnvPath
} else {
    $miniappProductionEnvPath
}
$miniappPath = Join-Path $projectRoot 'miniapp'

function Read-DotEnv([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            continue
        }
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$Matches[1]] = $value
    }
    return $values
}

function Is-Placeholder([string]$Value) {
    return [string]::IsNullOrWhiteSpace($Value) -or
        $Value -match 'CHANGE_ME|example\.com|your-domain|touristappid'
}

$errors = [System.Collections.Generic.List[string]]::new()
if (-not (Test-Path -LiteralPath $backendEnvPath)) {
    $errors.Add('Missing root .env.production.')
}
if (-not (Test-Path -LiteralPath $miniappEnvPath)) {
    $errors.Add('Missing miniapp/.env.production.local or miniapp/.env.production.')
}

$backendEnv = Read-DotEnv $backendEnvPath
$miniappEnv = Read-DotEnv $miniappEnvPath
$deployTarget = $backendEnv['DEPLOY_TARGET']
$usesCloudBase = $deployTarget -eq 'cloudbase'

if ($deployTarget -notin @('cloudbase', 'server')) {
    $errors.Add('DEPLOY_TARGET must be cloudbase or server.')
}
if ((Is-Placeholder $backendEnv['SECRET_KEY']) -or $backendEnv['SECRET_KEY'].Length -lt 32) {
    $errors.Add('SECRET_KEY must contain at least 32 characters.')
}
if (Is-Placeholder $backendEnv['DATABASE_URL']) {
    $errors.Add('DATABASE_URL is still a placeholder.')
}
if ($backendEnv['WECHAT_APP_ID'] -notmatch '^wx[a-zA-Z0-9]{16}$') {
    $errors.Add('WECHAT_APP_ID is invalid.')
}
if ((Is-Placeholder $backendEnv['WECHAT_APP_SECRET']) -or $backendEnv['WECHAT_APP_SECRET'].Length -lt 16) {
    $errors.Add('WECHAT_APP_SECRET is missing.')
}
if ($miniappEnv['TARO_APP_ID'] -ne $backendEnv['WECHAT_APP_ID']) {
    $errors.Add('Frontend and backend AppIDs do not match.')
}

$healthUrl = $null
if ($usesCloudBase) {
    if (Is-Placeholder $miniappEnv['TARO_APP_CLOUD_ENV']) {
        $errors.Add('TARO_APP_CLOUD_ENV must be a real CloudBase environment ID.')
    }
    if (Is-Placeholder $miniappEnv['TARO_APP_CLOUD_SERVICE']) {
        $errors.Add('TARO_APP_CLOUD_SERVICE must be a real CloudBase service name.')
    }

    if (-not $SkipRemoteHealth) {
        if (Is-Placeholder $backendEnv['CLOUDBASE_PUBLIC_BASE_URL']) {
            $errors.Add('CLOUDBASE_PUBLIC_BASE_URL is required unless -SkipRemoteHealth is used.')
        } else {
            $cloudBaseUri = $null
            try {
                $cloudBaseUri = [Uri]$backendEnv['CLOUDBASE_PUBLIC_BASE_URL']
            } catch {
                $errors.Add('CLOUDBASE_PUBLIC_BASE_URL is invalid.')
            }
            if ($cloudBaseUri) {
                if ($cloudBaseUri.Scheme -ne 'https') {
                    $errors.Add('CLOUDBASE_PUBLIC_BASE_URL must use HTTPS.')
                }
                if ($cloudBaseUri.Host -match '^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') {
                    $errors.Add('CLOUDBASE_PUBLIC_BASE_URL cannot use a local or private address.')
                }
                $healthUrl = "$($cloudBaseUri.AbsoluteUri.TrimEnd('/'))/health"
            }
        }
    }
} else {
    if (Is-Placeholder $backendEnv['DOMAIN']) {
        $errors.Add('DOMAIN must be a real public domain.')
    }
    if ($backendEnv['CADDY_EMAIL'] -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
        $errors.Add('CADDY_EMAIL is invalid.')
    }
    if (Is-Placeholder $backendEnv['POSTGRES_PASSWORD']) {
        $errors.Add('POSTGRES_PASSWORD is still a placeholder.')
    }

    $apiUri = $null
    try {
        $apiUri = [Uri]$miniappEnv['TARO_APP_API_BASE_URL']
    } catch {
        $errors.Add('TARO_APP_API_BASE_URL is invalid.')
    }
    if ($apiUri) {
        if ($apiUri.Scheme -ne 'https') {
            $errors.Add('The trial API must use HTTPS.')
        }
        if ($apiUri.AbsolutePath.TrimEnd('/') -ne '/api/v1') {
            $errors.Add('TARO_APP_API_BASE_URL must end with /api/v1.')
        }
        if ($apiUri.Host -match '^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') {
            $errors.Add('The trial API cannot use a local or private address.')
        }
        if ($backendEnv['DOMAIN'] -and $apiUri.Host -ne $backendEnv['DOMAIN']) {
            $errors.Add('The API hostname must match DOMAIN.')
        }
    }
    $healthUrl = "https://$($backendEnv['DOMAIN'])/health"
}

if ($errors.Count -gt 0) {
    Write-Host 'Trial release preflight failed:' -ForegroundColor Red
    foreach ($message in $errors) {
        Write-Host "  - $message" -ForegroundColor Red
    }
    exit 1
}

if (-not $SkipRemoteHealth) {
    Write-Host "Checking public health endpoint: $healthUrl"
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 15
    if ($health.status -ne 'ok') {
        throw "Public health endpoint returned an invalid response: $healthUrl"
    }
}

Push-Location $miniappPath
$hadBuildVersion = Test-Path Env:TARO_APP_BUILD_VERSION
$previousBuildVersion = $env:TARO_APP_BUILD_VERSION
try {
    if ($MiniappVersion) {
        $env:TARO_APP_BUILD_VERSION = $MiniappVersion
    }
    & pnpm.cmd typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Miniapp typecheck failed.' }

    & pnpm.cmd test:build-contract
    if ($LASTEXITCODE -ne 0) { throw 'Miniapp build contract tests failed.' }

    & pnpm.cmd build:weapp
    if ($LASTEXITCODE -ne 0) { throw 'Miniapp production build failed.' }

    $distConfig = Get-Content -LiteralPath 'dist\project.config.json' -Raw | ConvertFrom-Json
    if ($distConfig.appid -ne $backendEnv['WECHAT_APP_ID']) {
        throw 'Built AppID does not match backend configuration.'
    }
    if ($distConfig.setting.compileHotReLoad -ne $false) {
        throw 'compileHotReLoad must be disabled in the production build.'
    }

    $buildInfo = Get-Content -LiteralPath 'dist\build-info.json' -Raw | ConvertFrom-Json
    $headCommit = (& git -C $projectRoot rev-parse --verify HEAD).Trim().ToLowerInvariant()
    if ($buildInfo.schema_version -ne '1.0') {
        throw 'Miniapp build metadata schema is invalid.'
    }
    if ($MiniappVersion -and $buildInfo.build_version -ne $MiniappVersion) {
        throw 'Built miniapp version does not match the upload version.'
    }
    if ($buildInfo.build_commit -ne $headCommit) {
        throw 'Built miniapp commit does not match the current Git HEAD.'
    }
    if ($buildInfo.source_dirty -ne $false) {
        throw 'Refusing to release a miniapp built from a dirty source tree.'
    }

    $unsupported = Get-ChildItem -LiteralPath 'dist' -Filter '*.js' -Recurse |
        Select-String -SimpleMatch -Pattern '?.', '??'
    if ($unsupported) {
        throw 'Production build contains optional chaining or nullish coalescing.'
    }
} finally {
    if ($hadBuildVersion) {
        $env:TARO_APP_BUILD_VERSION = $previousBuildVersion
    } else {
        Remove-Item Env:TARO_APP_BUILD_VERSION -ErrorAction SilentlyContinue
    }
    Pop-Location
}

Write-Host 'Trial release preflight passed.' -ForegroundColor Green
