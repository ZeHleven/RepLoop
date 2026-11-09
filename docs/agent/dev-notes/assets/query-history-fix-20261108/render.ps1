$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$data = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'source.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$bitmap = New-Object System.Drawing.Bitmap(1200, 1190)
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

Draw-Text '循练 Agent：修复查询，保留失败' 48 35 40 '#173F39'
Draw-Text '本地合成任务使用真实模型 · 2026-11-08 · 非小程序现场截图' 50 96 22 '#526A63'
Draw-Text '同一组16个任务：完整完成数' 50 150 26 '#173F39'
for ($index = 0; $index -lt 3; $index++) {
    $item = $data.fixed_tasks[$index]
    $left = 50 + 375 * $index
    Fill-Box $left 198 350 169 '#FFFFFF'
    Draw-Text "$($item.label) / $($item.commit)" ($left + 22) 214 21 '#526A63' 310
    Draw-Text "$($item.completed) / $($item.total)" ($left + 22) 247 51 '#096F60' 300
    $caption = if ($item.partial -gt 0) { "另有$($item.partial)个部分完成" } else { '原问题已通过固定验收' }
    Draw-Text $caption ($left + 24) 322 20 '#526A63' 310
}

Fill-Box 50 399 1100 251 '#E2EFE7'
Draw-Text '只读追问恢复，已记录时长补齐' 74 419 28 '#173F39'
Draw-Text ('用户原文：' + $data.question_verbatim) 75 467 23 '#173F39' 1040
Draw-Text '原始回复节选（非完整回复；原文及定位保存在source.json）：' 75 515 19 '#526A63' 1040
Draw-Text $data.reply_rows_verbatim[0] 75 555 21 '#173F39' 1035 45
Draw-Text $data.reply_rows_verbatim[1] 75 594 21 '#173F39' 1035 45

Fill-Box 50 683 1100 203 '#FFF0DF'
Draw-Text "扩展新表达：$($data.extra.completed) / $($data.extra.total)完成，1个部分完成" 74 704 29 '#894B14'
Draw-Text '“过去一周完成过的训练”首轮仍被规则误拦。' 75 757 25 '#704B2D'
Draw-Text '相同响应回放到旧主线也失败；登记为下一轮问题。' 75 800 22 '#704B2D'
Draw-Text '保留全部失败和重试；未按新题继续修补、重跑换分。' 75 842 20 '#704B2D'

Draw-Text ("{0}项后端测试通过  ·  {1}次模型HTTP  ·  估算¥{2:F2}" -f $data.backend_tests, $data.total_http, $data.estimated_cny) 50 927 27 '#173F39'
Draw-Text '费用包含两个候选及扩展批次，按应用保守费率估算，非实际账单。' 50 975 20 '#526A63'
Draw-Text '实施者出题与复核，非独立盲测；真人反馈与手机端指标仍未采集。' 50 1020 20 '#526A63'
Draw-Text '图为指标示意，原回复仍包含组次等摘要；未单独评分最简列输出。' 50 1061 20 '#526A63'
Draw-Text '来源：原基线及三批原始结果、audit.json、完整回归与无网络回放。' 50 1117 18 '#526A63'

$bitmap.Save((Join-Path $PSScriptRoot 'metrics.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
Write-Output 'Rendered metrics.png (1200 x 1190) from source.json'
