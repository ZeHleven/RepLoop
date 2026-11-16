$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$evidence = Join-Path $repoRoot 'artifacts/agent-dev-notes/rest-release-acceptance-20261111'
$v = Get-Content -LiteralPath (Join-Path $evidence 'validation.json') -Raw | ConvertFrom-Json
$assetDir = Join-Path $repoRoot 'docs/agent/dev-notes/assets/rest-release-acceptance-20261111'
New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
$target = Join-Path $assetDir 'acceptance-results.png'
if (Test-Path -LiteralPath $target) { throw 'Preserve existing diagram.' }
$canvas = [System.Drawing.Bitmap]::new(1400, 1000)
$g = [System.Drawing.Graphics]::FromImage($canvas)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$g.Clear([System.Drawing.ColorTranslator]::FromHtml('#F5F7F4'))
$fonts = @{}
foreach ($size in @(20,24,28,32,44)) {
    $fonts[$size] = [System.Drawing.Font]::new('Microsoft YaHei', $size, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
}
function Label([string]$Text, [int]$Size, [string]$Color, [int]$X, [int]$Y, [int]$Width, [int]$Height) {
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Color))
    $g.DrawString($Text, $fonts[$Size], $brush, [System.Drawing.RectangleF]::new($X,$Y,$Width,$Height))
    $brush.Dispose()
}
function Box([int]$X, [int]$Y, [string]$Fill, [string]$Title, [string]$Detail) {
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($Fill))
    $g.FillRectangle($brush,$X,$Y,620,160)
    $brush.Dispose()
    Label $Title 32 '#193F31' ($X+24) ($Y+22) 575 50
    Label $Detail 24 '#53645C' ($X+24) ($Y+84) 575 65
}
Label '同一候选：功能门禁与答复审阅' 44 '#153D2E' 54 36 1290 70
Label '2f8eca1 · 合成账号真实模型验收 · 结果示意，非产品截图' 24 '#66756C' 58 112 1280 44
$completed = $v.preparation.quality_outcomes.completed
$partial = $v.preparation.quality_outcomes.partial
Box 60 190 '#F2E6DF' "准备任务：$completed 完成 / $partial 部分" '4 条有计划准备建议的组间范围与原值正确；综合审阅发现两处过度推断。'
Box 724 190 '#D6E9DD' "路由：$($v.route.passed_count) / $($v.route.total)" "关键题 $($v.route.critical_passed) / $($v.route.critical_total)；普通请求规则回退 $($v.route.ordinary_rules_fallbacks)。"
for ($i=0; $i -lt 2; $i++) {
    $pass = $v.meal_passes[$i]
    $r = $pass.original_report
    $successes = if ($r) { $r.original_successes + $r.synonym_successes } else { '待完整结果' }
    $fill = if ($pass.original_aggregate_passed) { '#D6E9DD' } else { '#F2E6DF' }
    Box (60 + 664*$i) 378 $fill "饮食第 $($i+1) 轮：$successes / 31" '按原脚本门槛验收；原句 10 次、同义表达 21 项。'
}
Label '仍待修复的两处推断' 32 '#83594C' 60 588 1280 48
Label '① 本周无关联完成日 → 推成计划内首次训练；统计范围被扩大。' 28 '#5D5149' 60 650 1280 48
Label '② 计划休息 150 秒 → 保证每组质量做满；缺少个人恢复依据。' 28 '#5D5149' 60 708 1280 48
$cost = '{0:F6}' -f ($v.budget.known_estimated_nano / 1e9)
Label "$($v.budget.http) / 300 次 HTTP · 估算 ¥$cost / ¥4 · 未合并、未部署" 28 '#216B4E' 60 794 1280 48
Label '一条时钟异常保持空值；新发现的恢复保证问题与预登记休息检查分开记录。' 24 '#4C6255' 60 860 1280 48
Label '来源：rest-release-acceptance-20261111 原始回复、工具轨迹、原门禁报告与SQL账本。' 20 '#6E7D73' 60 940 1280 36
$canvas.Save($target,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $canvas.Dispose()
foreach ($font in $fonts.Values) { $font.Dispose() }
Write-Output $target
