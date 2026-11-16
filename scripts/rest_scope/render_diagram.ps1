$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$assetDir = Join-Path $repoRoot 'docs/agent/dev-notes/assets/preparation-rest-scope-20261111'
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
$target = Join-Path $assetDir 'rest-scope-contract.png'
if (Test-Path -LiteralPath $target) { throw 'Preserve existing diagram.' }
$canvas = [System.Drawing.Bitmap]::new(1400, 900)
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
    $g.FillRectangle($brush, $X,$Y,620,160)
    $brush.Dispose()
    Write-Label $Title 28 '#193F31' ($X+24) ($Y+18) 575 48
    Write-Label $Detail 24 '#53645C' ($X+24) ($Y+75) 575 78
}
Write-Label '90 秒没有变，休息的含义变了' 46 '#153D2E' 54 36 1290 70
Write-Label '准备建议的字段范围修复  ·  合成机制示意  ·  非产品截图' 24 '#66756C' 58 112 1280 44
Write-Label '旧候选真实失败片段' 32 '#83594C' 60 192 615 48
Write-Label '离线修复与验证' 32 '#216B4E' 724 192 615 48
Draw-Box 60 254 '#F2E6DF' '先说“组间休息 90 秒”' '随后又说“两次动作之间按计划休息90秒”。数字相同，范围漂移。'
Draw-Box 724 254 '#D6E9DD' '工具说明 rest_seconds 的含义' '单位为秒；同一动作相邻组之间休息；每个动作保留自己的计划原值。'
Draw-Box 60 438 '#E9EDE7' '合成夹具：45 秒 / 150 秒' '用不同动作的不同值检查读取与传递，避免所有动作被概括成同一时长。'
Draw-Box 724 438 '#D6E9DD' '三条答复路径都收到事实' '直接答复、补读后答复、计划最终答复：捕获实际模型输入进行核对。'
Write-Label '有效基线：5 失败 / 1 通过 → 修复后 6 通过；相关回归 60 通过' 28 '#216B4E' 60 644 1280 52
Write-Label '仅证明语义约束传递；真实模型是否稳定遵守，仍待新增预算后的复测。' 28 '#83594C' 60 716 1280 55
Write-Label '本轮模型 HTTP 0 次 / ¥0；未部署。原失败与测试夹具错误均保留。' 24 '#4C6255' 60 790 1280 44
Write-Label '来源：preparation-rest-scope-20261111 开发笔记；原文来自5816ac5的P01隔离实测。' 20 '#6E7D73' 60 852 1280 34
$canvas.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$canvas.Dispose()
foreach ($font in $fonts.Values) { $font.Dispose() }
Write-Output $target
