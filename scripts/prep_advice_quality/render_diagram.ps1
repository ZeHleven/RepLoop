$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$assetDir = Join-Path $repoRoot 'docs/agent/dev-notes/assets/preparation-advice-quality-20261110'
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
$target = Join-Path $assetDir 'advice-verification.png'
if (Test-Path -LiteralPath $target) { throw 'Preserve existing diagram.' }
$canvas = [System.Drawing.Bitmap]::new(1400, 880)
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
    $g.FillRectangle($brush, $X,$Y,620,118)
    $brush.Dispose()
    Write-Label $Title 28 '#193F31' ($X+24) ($Y+15) 575 44
    Write-Label $Detail 20 '#53645C' ($X+24) ($Y+65) 575 44
}
Write-Label '能返回答案之后，还要验什么？' 46 '#153D2E' 54 36 1290 70
Write-Label '训练准备建议  ·  合成数据实测与机制示意  ·  非产品截图' 24 '#66756C' 58 112 1280 44
Write-Label '已知失败样本' 32 '#83594C' 60 194 615 50
Write-Label '候选修复与验证' 32 '#216B4E' 724 194 615 50
Draw-Box 60 262 '#F2E6DF' '卡片有了，计划证据仍缺' '对话结束前没有补齐所需的当前计划'
Draw-Box 724 262 '#D6E9DD' '有界补读，再据实回答' '真实五轮会话触发补读，成功交付下一练建议'
Draw-Box 60 402 '#F2E6DF' '把公斤数当成个人强度' '动作变形后仍要求把目标次数做满'
Draw-Box 724 402 '#D6E9DD' '说明未知，优先动作质量' '不凭绝对重量判轻重；失去控制时不凑次数'
Draw-Box 60 542 '#F2E6DF' '计划完成数被当成全部历史' '当前计划关联为 0，不等于用户从未完成训练'
Draw-Box 724 542 '#D6E9DD' '保留统计范围与无计划状态' '计划周统计明确范围；无计划时不编造下一练'
Write-Label '2118 项离线测试通过  ·  两项真实补测完成  ·  下一练本地等待 11.3 秒' 28 '#216B4E' 60 704 1280 48
Write-Label '候选 5816ac5，尚未部署；手机仍需复验。单次已知回归样本，不代表生产成功率。' 24 '#4C6255' 60 770 1280 46
Write-Label '来源：本轮开发笔记、live-01 原始回复 / 工具轨迹 / SQL 与账本；公众号素材仅本地准备。' 20 '#6E7D73' 60 828 1280 36
$canvas.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$canvas.Dispose()
foreach ($font in $fonts.Values) { $font.Dispose() }
Write-Output $target
