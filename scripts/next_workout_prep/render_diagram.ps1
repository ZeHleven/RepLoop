$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$assetDir = Join-Path $repoRoot 'docs/agent/dev-notes/assets/next-workout-prep-20261110'
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
$target = Join-Path $assetDir 'required-read-flow.png'
if (Test-Path -LiteralPath $target) { throw 'Keep the existing diagram; use a new filename.' }
$canvas = [System.Drawing.Bitmap]::new(1400, 840)
$g = [System.Drawing.Graphics]::FromImage($canvas)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$g.Clear([System.Drawing.ColorTranslator]::FromHtml('#F5F7F4'))
$fonts = @{}
foreach ($size in @(20,24,28,32,46)) {
    $fonts[$size] = [System.Drawing.Font]::new('Microsoft YaHei', $size, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
}
function Write-Label([string]$Text, [int]$Size, [string]$Color, [int]$X, [int]$Y, [int]$Width, [int]$Height) {
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Color))
    $g.DrawString($Text, $fonts[$Size], $brush, [System.Drawing.RectangleF]::new($X,$Y,$Width,$Height))
    $brush.Dispose()
}
function Draw-Box([int]$X, [int]$Y, [string]$Fill, [string]$Title, [string]$Detail) {
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Fill))
    $g.FillRectangle($brush, $X,$Y,620,112); $brush.Dispose()
    Write-Label $Title 28 '#193F31' ($X+24) ($Y+15) 575 44
    Write-Label $Detail 20 '#53645C' ($X+24) ($Y+64) 575 38
}
function Draw-Arrow([int]$X,[int]$Y) {
    $pen=[System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#8B9A91'),3)
    $g.DrawLine($pen,$X,$Y,$X,$Y+26)
    $g.DrawLine($pen,$X,$Y+26,$X-7,$Y+18)
    $g.DrawLine($pen,$X,$Y+26,$X+7,$Y+18)
    $pen.Dispose()
}
Write-Label '为什么有卡片，却没有建议？' 46 '#153D2E' 54 38 1280 70
Write-Label 'QUERY-20261110-01  ·  执行机制示意图  ·  本地开发笔记' 24 '#66756C' 58 118 1260 44
Write-Label '修复前：合成遗漏回放' 32 '#83594C' 60 198 615 50
Write-Label '候选修复：有限补齐' 32 '#216B4E' 724 198 615 50
Draw-Box 60 266 '#E7EEE8' '下一练查询成功' '卡片可以展示，但整份计划仍未取得'
Draw-Arrow 370 383
Draw-Box 60 416 '#F2E6DF' '当前计划未读取' '执行器结束，缺项没有得到补查'
Draw-Arrow 370 533
Draw-Box 60 566 '#F2E6DF' '最终检查拦截回答' '只显示资料不全提示，没有交付准备建议'
Draw-Box 724 266 '#E0EEE4' '结束前检查必读清单' '保留原先的资料要求，不把卡片冒充完整证据'
Draw-Arrow 1034 383
Draw-Box 724 416 '#D6E9DD' '在权限和预算内补读' '只补无参数、未尝试的缺项；错误不自动重试'
Draw-Arrow 1034 533
Draw-Box 724 566 '#C6E2D0' '真实结果交给回答阶段' '保留工具审计；原先完整的任务不额外调用'
Write-Label '合成回放 ≠ 真实手机验收。此图不是产品截图，线上仍需部署和复验。' 24 '#4C6255' 60 735 1280 44
Write-Label '依据：本轮修改前登记、before-fixture-corrected 与 after-core 原始回放；不含用户真实训练资料。' 20 '#6E7D73' 60 784 1280 35
$canvas.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $canvas.Dispose()
foreach ($font in $fonts.Values) { $font.Dispose() }
Write-Output $target
