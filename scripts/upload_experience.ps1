[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Description,

    [switch]$SkipRemoteHealth,

    [string]$DeveloperToolsPath = 'D:\WeChatProgram\微信web开发者工具\cli.bat'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_version.ps1')
$projectVersion = Get-ProjectVersion
if ($Version -and $Version -ne $projectVersion) {
    throw 'Requested upload version differs from backend/VERSION.'
}
$Version = $projectVersion
$projectRoot = Split-Path -Parent $PSScriptRoot
$miniappPath = Join-Path $projectRoot 'miniapp'
$preflightPath = Join-Path $PSScriptRoot 'release_preflight.ps1'

if (-not (Test-Path -LiteralPath $DeveloperToolsPath)) {
    throw "WeChat DevTools CLI not found: $DeveloperToolsPath"
}

& $preflightPath `
    -SkipRemoteHealth:$SkipRemoteHealth `
    -MiniappVersion $Version
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $DeveloperToolsPath upload `
    --project $miniappPath `
    --version $Version `
    --desc $Description `
    --lang zh

if ($LASTEXITCODE -ne 0) {
    throw 'Upload failed. Confirm that DevTools is signed in and its service port is enabled.'
}

Write-Host "Development version $Version uploaded. Select it as the trial version in WeChat Admin." -ForegroundColor Green
