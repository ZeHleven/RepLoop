$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$v = Get-Content -LiteralPath (Join-Path $repoRoot 'artifacts/agent-dev-notes/preparation-release-acceptance-20261110/validation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$assetDir = Join-Path $repoRoot 'docs/agent/dev-notes/assets/preparation-release-acceptance-20261110'
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
$target = Join-Path $assetDir 'acceptance-results-v2.png'
if (Test-Path -LiteralPath $target) { throw 'Preserve existing diagram.' }
$canvas = [System.Drawing.Bitmap]::new(1400, 900)
$g = [System.Drawing.Graphics]::FromImage($canvas)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$g.Clear([System.Drawing.ColorTranslator]::FromHtml('#F5F7F4'))
$fonts = @{}
foreach ($size in @(20,24,28,32,42)) {
    $fonts[$size] = [System.Drawing.Font]::new('Microsoft YaHei', $size, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
}
function Label([string]$Text, [int]$Size, [string]$Color, [int]$X, [int]$Y, [int]$Width, [int]$Height) {
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Color))
    $g.DrawString($Text, $fonts[$Size], $brush, [System.Drawing.RectangleF]::new($X,$Y,$Width,$Height))
    $brush.Dispose()
}
function Row([int]$Y, [string]$Fill, [string]$Name, [string]$Result, [string]$Detail) {
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Fill))
    $g.FillRectangle($brush, 60,$Y,1280,112)
    $brush.Dispose()
    Label $Name 28 '#193F31' 84 ($Y+18) 250 48
    Label $Result 28 '#193F31' 350 ($Y+18) 950 48
    Label $Detail 20 '#53645C' 350 ($Y+67) 950 36
}
Label '同一轮验收：成功、缺陷与预算停止' 42 '#153D2E' 54 36 1300 65
Label '候选 5816ac5  ·  合成数据 / 本地真实模型  ·  结果示意图，非产品截图' 24 '#66756C' 58 116 1280 44
Row 194 '#F2E6DF' '准备建议' '短句部分完成；长句完成' '均已交付建议；短句把“组间休息”误写成“动作之间休息”'
Row 324 '#D6E9DD' '路由验收' "$($v.route.passed_count) / $($v.route.total) 通过；关键题 $($v.route.critical_passed) / $($v.route.critical_total)" '使用冻结原脚本判分，无普通请求规则回退'
Row 454 '#D6E9DD' '饮食第一轮' "$($v.meal_passes[0].artifact_successes_observed) / 31 完成，原门槛通过" '原句 10 次 + 21 种表达；未确认饮食写入与提案均为 0'
$second = $v.meal_passes[1]
$secondResult = if ($second.original_aggregate_passed -eq $true) { '31 / 31 完成，原门槛通过' } else { "$($second.artifact_successes_observed) / 31 已成功交付，整轮未完成" }
Row 584 '#F2E6DF' '饮食第二轮' $secondResult '第 30 项预算中止，第 31 项未运行；整轮没有通过结论'
$cost = '{0:F6}' -f ($v.budget.known_estimated_nano / 1000000000)
Label "$($v.budget.http) 次模型 HTTP  ·  已知估算 ¥$cost  ·  本轮授权上限 ¥3" 28 '#216B4E' 60 738 1280 48
Label '按最坏费用预留后再发送；余额不足即停。没有为拿到绿灯而重跑。' 24 '#4C6255' 60 802 1280 42
Label '依据：原始回复、工具轨迹、原脚本判分及本地账本。未合并、未部署、未新增手机体验样本。' 20 '#6E7D73' 60 854 1280 34
$canvas.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$canvas.Dispose()
foreach ($font in $fonts.Values) { $font.Dispose() }
Write-Output $target
