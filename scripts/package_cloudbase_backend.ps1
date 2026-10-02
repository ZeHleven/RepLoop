param(
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]{0,39}$')]
    [string]$Version = "0.5.46",
    [ValidateRange(0.01, 1024)]
    [double]$MaxPackageSizeMB = 25
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendPath = (Resolve-Path (Join-Path $repoRoot "backend")).Path
$outputDirectory = Join-Path $repoRoot "deploy\cloudbase"
$outputPath = Join-Path $outputDirectory "fitness-agent-backend-$Version.zip"

function Assert-ZipEntryBoundaries {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $stream = [System.IO.File]::Open(
        $resolvedPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $reader = [System.IO.BinaryReader]::new($stream)
    try {
        if ($stream.Length -lt 22) {
            throw "ZIP is too small to contain an end-of-central-directory record."
        }

        $tailLength = [int][Math]::Min($stream.Length, 65557)
        [void]$stream.Seek(-$tailLength, [System.IO.SeekOrigin]::End)
        $tail = $reader.ReadBytes($tailLength)
        $eocdIndex = -1
        for ($index = $tail.Length - 22; $index -ge 0; $index--) {
            if (
                $tail[$index] -eq 0x50 -and
                $tail[$index + 1] -eq 0x4b -and
                $tail[$index + 2] -eq 0x05 -and
                $tail[$index + 3] -eq 0x06
            ) {
                $commentLength = [BitConverter]::ToUInt16($tail, $index + 20)
                if ($index + 22 + $commentLength -eq $tail.Length) {
                    $eocdIndex = $index
                    break
                }
            }
        }
        if ($eocdIndex -lt 0) {
            throw "ZIP end-of-central-directory record was not found."
        }

        $eocdOffset = $stream.Length - $tailLength + $eocdIndex
        $stream.Position = $eocdOffset + 4
        $diskNumber = $reader.ReadUInt16()
        $centralDirectoryDisk = $reader.ReadUInt16()
        $entriesOnDisk = $reader.ReadUInt16()
        $entryCount = $reader.ReadUInt16()
        $centralDirectorySize = $reader.ReadUInt32()
        $centralDirectoryOffset = $reader.ReadUInt32()
        [void]$reader.ReadUInt16()

        if (
            $diskNumber -ne 0 -or
            $centralDirectoryDisk -ne 0 -or
            $entriesOnDisk -ne $entryCount -or
            $entryCount -eq [UInt16]::MaxValue
        ) {
            throw "Multi-disk or ZIP64 archives are not supported by this gate."
        }
        if (
            [uint64]$centralDirectoryOffset +
            [uint64]$centralDirectorySize -gt [uint64]$eocdOffset
        ) {
            throw "ZIP central directory overlaps the end record."
        }

        $centralEntries = [System.Collections.Generic.List[object]]::new()
        $stream.Position = $centralDirectoryOffset
        for ($entryIndex = 0; $entryIndex -lt $entryCount; $entryIndex++) {
            if ($reader.ReadUInt32() -ne 0x02014b50) {
                throw "ZIP central directory entry $entryIndex is invalid."
            }
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            $flags = $reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt32()
            $compressedSize = $reader.ReadUInt32()
            [void]$reader.ReadUInt32()
            $nameLength = $reader.ReadUInt16()
            $extraLength = $reader.ReadUInt16()
            $commentLength = $reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt32()
            $localHeaderOffset = $reader.ReadUInt32()

            if (
                $compressedSize -eq [UInt32]::MaxValue -or
                $localHeaderOffset -eq [UInt32]::MaxValue
            ) {
                throw "ZIP64 entries are not supported by this gate."
            }
            $nameBytes = $reader.ReadBytes($nameLength)
            $entryName = [System.Text.Encoding]::UTF8.GetString($nameBytes)
            [void]$stream.Seek(
                $extraLength + $commentLength,
                [System.IO.SeekOrigin]::Current
            )
            $centralEntries.Add([pscustomobject]@{
                Name = $entryName
                Flags = $flags
                CompressedSize = [uint64]$compressedSize
                LocalHeaderOffset = [uint64]$localHeaderOffset
            })
        }

        $orderedEntries = @(
            $centralEntries | Sort-Object LocalHeaderOffset
        )
        for ($entryIndex = 0; $entryIndex -lt $orderedEntries.Count; $entryIndex++) {
            $entry = $orderedEntries[$entryIndex]
            $stream.Position = [int64]$entry.LocalHeaderOffset
            if ($reader.ReadUInt32() -ne 0x04034b50) {
                throw "ZIP local header is invalid for $($entry.Name)."
            }
            [void]$reader.ReadUInt16()
            $localFlags = $reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt16()
            [void]$reader.ReadUInt32()
            [void]$reader.ReadUInt32()
            [void]$reader.ReadUInt32()
            $localNameLength = $reader.ReadUInt16()
            $localExtraLength = $reader.ReadUInt16()
            if (($localFlags -band 0x08) -ne ($entry.Flags -band 0x08)) {
                throw "ZIP flag mismatch for $($entry.Name)."
            }

            $dataEnd = (
                $entry.LocalHeaderOffset + 30 + $localNameLength +
                $localExtraLength + $entry.CompressedSize
            )
            $descriptorLength = 0
            if (($entry.Flags -band 0x08) -ne 0) {
                $stream.Position = [int64]$dataEnd
                $descriptorSignature = $reader.ReadUInt32()
                $descriptorLength = if ($descriptorSignature -eq 0x08074b50) {
                    16
                }
                else {
                    12
                }
            }
            $entryEnd = $dataEnd + $descriptorLength
            $nextBoundary = if ($entryIndex + 1 -lt $orderedEntries.Count) {
                $orderedEntries[$entryIndex + 1].LocalHeaderOffset
            }
            else {
                [uint64]$centralDirectoryOffset
            }
            if ($entryEnd -gt $nextBoundary) {
                throw (
                    "ZIP entry overlaps the next component: " +
                    "$($entry.Name) ends at $entryEnd, boundary is $nextBoundary."
                )
            }
        }
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

$excludedDirectoryNames = @(
    ".git",
    ".venv",
    "venv",
    "tests",
    ".uv-cache",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".hypothesis",
    "__pycache__",
    "htmlcov",
    "dist",
    "build",
    "node_modules",
    ".pnpm-store"
)
$excludedFilePatterns = @(
    ".env",
    ".env.*",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.db",
    "*.sqlite3",
    ".coverage",
    ".coverage.*",
    "coverage.xml",
    ".DS_Store",
    "Thumbs.db"
)
$tarExcludePatterns = @($excludedDirectoryNames) + @($excludedFilePatterns) + @("*.egg-info")

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Force
}

$sourceMetadataPath = Join-Path $backendPath "app\build_metadata.json"
if (Test-Path -LiteralPath $sourceMetadataPath) {
    throw "Refusing to package a source-tree build_metadata.json file."
}

$buildCommit = (& git -C $repoRoot rev-parse --verify HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $buildCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Failed to resolve a full Git commit for build metadata."
}
$gitStatus = @(& git -C $repoRoot status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect the Git worktree for build metadata."
}
$sourceDirty = $gitStatus.Count -gt 0
$buildMetadata = [ordered]@{
    schema_version = "1.0"
    build_version = $Version
    build_commit = $buildCommit
    source_dirty = $sourceDirty
} | ConvertTo-Json -Compress

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRootPrefix = $tempRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
$metadataStagingRoot = Join-Path $tempRoot (
    "fitness-agent-cloudbase-" + [Guid]::NewGuid().ToString("N")
)
$metadataStagingRoot = [System.IO.Path]::GetFullPath($metadataStagingRoot)
if (-not $metadataStagingRoot.StartsWith(
    $tempRootPrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to create build metadata outside the temporary directory."
}
$metadataStagingApp = Join-Path $metadataStagingRoot "app"
New-Item -ItemType Directory -Force -Path $metadataStagingApp | Out-Null

$tarArguments = @("-a", "-c", "-f", $outputPath)
foreach ($pattern in $tarExcludePatterns) {
    $tarArguments += "--exclude=$pattern"
}
$tarArguments += @("-C", $backendPath, ".")
$tarArguments += @(
    "-C",
    $metadataStagingRoot,
    "./app/build_metadata.json"
)
try {
    [System.IO.File]::WriteAllText(
        (Join-Path $metadataStagingApp "build_metadata.json"),
        $buildMetadata,
        [System.Text.UTF8Encoding]::new($false)
    )
    & tar @tarArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create CloudBase package."
    }
}
catch {
    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Force
    }
    throw
}
finally {
    if (Test-Path -LiteralPath $metadataStagingRoot) {
        Remove-Item -LiteralPath $metadataStagingRoot -Recurse -Force
    }
}

try {
    Assert-ZipEntryBoundaries -Path $outputPath
}
catch {
    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Force
    }
    throw
}

$entries = @(& tar -tf $outputPath)
$forbidden = $entries | Where-Object {
    $normalizedEntry = $_ -replace '\\', '/'
    $normalizedEntry = $normalizedEntry -replace '^\./', ''
    $segments = @($normalizedEntry.Split('/', [System.StringSplitOptions]::RemoveEmptyEntries))
    $leafName = if ($segments.Count -gt 0) { $segments[-1] } else { "" }

    $containsExcludedDirectory = @(
        $segments | Where-Object { $excludedDirectoryNames -contains $_ }
    ).Count -gt 0
    $containsEggInfo = @(
        $segments | Where-Object { $_ -like "*.egg-info" }
    ).Count -gt 0
    $matchesExcludedFile = @(
        $excludedFilePatterns | Where-Object { $leafName -like $_ }
    ).Count -gt 0

    $containsExcludedDirectory -or $containsEggInfo -or $matchesExcludedFile
}
if ($forbidden) {
    Remove-Item -LiteralPath $outputPath -Force
    throw "Package contains forbidden entries: $($forbidden -join ', ')"
}

$required = @(
    "./Dockerfile",
    "./alembic/versions/0013_agent_run_queue.py",
    "./alembic/versions/0014_agent_run_serialization.py",
    "./alembic/versions/0015_agent_conversation_understanding.py",
    "./alembic/versions/0016_agent_run_result_uniqueness.py",
    "./alembic/versions/0017_agent_execution_trace.py",
    "./alembic/versions/0018_agent_tool_call_uniqueness.py",
    "./alembic/versions/0019_agent_intent_error_category.py",
    "./alembic/versions/0020_agent_proposal_lifecycle.py",
    "./alembic/versions/0021_agent_intent_semantics.py",
    "./alembic/versions/0022_general_proposals.py",
    "./alembic/versions/0023_food_aliases_and_intent_v4.py",
    "./alembic/versions/0024_agent_artifacts_and_evidence_v5.py",
    "./alembic/versions/0025_agent_understanding_v6.py",
    "./alembic/versions/0026_workout_adaptive_proposals.py",
    "./alembic/versions/0030_expand_strength_exercises.py",
    "./alembic/versions/0031_expand_food_library.py",
    "./alembic/versions/0032_agent_queue_position.py",
    "./app/schemas/agent_task.py",
    "./app/services/agent_evidence_contract.py",
    "./app/services/agent_plan_completeness.py",
    "./app/services/agent_plan_quantities.py",
    "./app/services/agent_run_order.py",
    "./app/services/agent_task_state.py",
    "./app/services/business_clock.py",
    "./app/services/history_status_scope.py",
    "./app/services/structured_schema.py",
    "./app/data/food_catalog_v1.json",
    "./app/data/FOOD-DATA-NOTICES.md",
    "./app/services/food_catalog_v1.py",
    "./app/services/food_library.py",
    "./app/data/exercise_catalog_v1.json",
    "./app/services/exercise_catalog_v1.py",
    "./app/services/exercise_search.py",
    "./app/config.py",
    "./app/build_metadata.json",
    "./app/startup_diagnostics.py",
    "./app/routers/agent.py",
    "./app/routers/proposals.py",
    "./app/schemas/agent_plan_adjustment_proposal.py",
    "./app/schemas/agent_plan_adjustment_proposal_api.py",
    "./app/services/agent_jobs.py",
    "./app/services/agent_controller.py",
    "./app/services/agent_planner.py",
    "./app/services/agent_plan_adjustment_proposal_decisions.py",
    "./app/services/agent_plan_adjustment_proposal_execution.py",
    "./app/services/agent_plan_adjustment_proposal_persistence.py",
    "./app/services/agent_plan_adjustment_proposals.py",
    "./app/services/agent_runtime.py",
    "./app/services/agent_intent.py",
    "./app/services/agent_intent_model.py",
    "./app/services/ai_client.py",
    "./app/services/agent_change_validation.py",
    "./app/services/agent_daily_meal_plans.py",
    "./app/services/daily_meal_optimizer.py",
    "./app/services/agent_domain_proposals.py",
    "./app/services/plan_management_proposals.py",
    "./app/services/agent_tool_registry.py",
    "./app/services/agent_tool_registry_read_authority.py",
    "./app/services/agent_tool_registry_read_enforcement.py",
    "./app/services/agent_tool_registry_shadow.py",
    "./app/services/agent_tool_registry_shadow_facts.py",
    "./app/services/agent_tool_registry_shadow_trace.py",
    "./app/services/agent_tool_registry_shadow_metrics.py",
    "./app/services/agent_tool_registry_shadow_metric_adapter.py",
    "./app/services/agent_tool_registry_shadow_observation.py",
    "./app/services/agent_trace.py",
    "./app/schemas/agent_planning.py",
    "./app/schemas/agent_tool_registry.py",
    "./app/schemas/agent_trace.py",
    "./scripts/summarize_registry_shadow_metrics.py",
    "./scripts/start_server.sh"
)
foreach ($entry in $required) {
    if ($entry -notin $entries) {
        Remove-Item -LiteralPath $outputPath -Force
        throw "Package is missing required entry: $entry"
    }
}

$file = Get-Item -LiteralPath $outputPath
$maxPackageSizeBytes = $MaxPackageSizeMB * 1MB
if ($file.Length -gt $maxPackageSizeBytes) {
    $actualSizeMB = [math]::Round($file.Length / 1MB, 2)
    Remove-Item -LiteralPath $outputPath -Force
    throw "Package size $actualSizeMB MiB exceeds the configured limit of $MaxPackageSizeMB MiB."
}

Write-Host "Created $($file.FullName) ($($file.Length) bytes, $($entries.Count) entries)"
