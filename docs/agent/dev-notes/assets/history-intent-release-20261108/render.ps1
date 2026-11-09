$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$data = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'source.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$bitmap = New-Object System.Drawing.Bitmap(1200, 1230)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#F3F5F1'))
function Fill-Box($x, $y, $width, $height, $color) {
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml($color))
    $graphics.FillRectangle($brush, $x, $y, $width, $height)
    $brush.Dispose()
}
function Draw-Text($value, $x, $y, $size, $color, $width = 1090, $height = 90) {
    $font = New-Object System.Drawing.Font('Microsoft YaHei UI', $size, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml($color))
    $rect = New-Object System.Drawing.RectangleF($x, $y, $width, $height)
    $graphics.DrawString([string]$value, $font, $brush, $rect)
    $font.Dispose()
    $brush.Dispose()
}
Draw-Text '循练 Agent：记录是真的，日期也会查错' 48 35 39 '#173F39'
Draw-Text '开发笔记 · 2026-11-08  /  业务日 2026-11-09（上海）' 50 98 22 '#526A63'
Draw-Text '本地合成任务 × 真实模型 · 指标示意，非小程序截图' 50 139 22 '#526A63'

Fill-Box 50 210 535 218 '#FFF0DF'
Fill-Box 610 210 540 218 '#DEEEE4'
Draw-Text '第一候选 137461d' 75 233 27 '#894B14' 490
Draw-Text '已知查询回归  2 / 3' 75 286 37 '#894B14' 490
Draw-Text '原16项通过 · 空任务恢复1/1' 75 348 24 '#704B2D' 490
Draw-Text '新措辞首次3/3（非盲测）' 75 386 22 '#704B2D' 490
Draw-Text '最终候选 f4d388f' 635 233 27 '#173F39' 490
Draw-Text '已知查询回归  3 / 3' 635 286 37 '#173F39' 490
Draw-Text '原16项通过 · 空任务恢复1/1' 635 348 24 '#173F39' 490
Draw-Text '相同措辞复测3/3' 635 386 22 '#526A63' 490

Fill-Box 50 467 1100 281 '#FFFFFF'
Draw-Text '被完整保留的一次失败' 75 489 29 '#173F39'
Draw-Text ('用户原文：' + $data.question_verbatim) 75 540 23 '#173F39' 1040
Draw-Text '第一候选实际：11月8日 · 12分钟（查错日期）' 75 599 28 '#894B14'
Draw-Text '最终候选实际：11月7日 · 30分钟（符合前天）' 75 650 28 '#173F39'
Draw-Text '上面是结果摘要；完整原回复及定位保存在 source.json。' 75 706 19 '#526A63'

Draw-Text ("{0}项后端测试通过 · {1}次模型HTTP · 估算¥{2:F2}" -f $data.backend_tests, $data.total_http, $data.estimated_cny) 50 801 29 '#173F39'
Draw-Text '费用含两个候选与全部错误，按应用保守费率估算，非供应商账单。' 50 851 21 '#526A63'

Fill-Box 50 915 1100 185 '#E7ECE7'
Draw-Text '这张图没有证明的事' 75 934 26 '#173F39'
Draw-Text '最终模型首次即答对；纠错机制另用旧错误响应回放验证。' 75 981 22 '#526A63'
Draw-Text '同一实施者出题与复核；真人反馈、手机端等待仍未采集。' 75 1023 22 '#526A63'
Draw-Text '来源：四批原始结果、人工判分、audit.json、2082项回归。' 50 1141 20 '#526A63'
$bitmap.Save((Join-Path $PSScriptRoot 'metrics.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
Write-Output 'Rendered metrics.png (1200 x 1230)'
