param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$OutputRoot = (Join-Path $RepoRoot "outputs\ba_colmap_release"),
    [string]$PlanCsv = "",
    [int]$Seed = 20260603,
    [double]$Tolerance = 0.02,
    [int]$FullRefineIterations = 12,
    [int]$FullSearchSteps = 0,
    [int]$MismatchFullSearchSteps = 4,
    [string]$StaticFilePolicy = "hardlink",
    [switch]$RetryExact,
    [switch]$MissingOnly
)

$ErrorActionPreference = "Stop"

if (-not $PlanCsv) {
    $PlanCsv = Join-Path $OutputRoot "ba_colmap_quality_plan.csv"
}

$DetailCsv = Join-Path $OutputRoot "ba_colmap_quality_release_status.csv"
$SummaryCsv = Join-Path $OutputRoot "ba_colmap_quality_release_dataset_summary.csv"
$Generator = Join-Path $RepoRoot "tools\pvl_ba_quality\generate_noisy_pvl_ba.py"
$LogDir = Join-Path $OutputRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "resume_quality_$RunStamp.log"

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $LogPath -Append
}

function Get-VariantName {
    param(
        [string]$Dataset,
        [int]$Target
    )
    $tail = $Dataset -replace "^ba-colmap-problem-", "" -replace "-c0$", ""
    if ($Target -le 100) {
        $tag = "init-rmse"
    } else {
        $tag = "joint-rmse"
    }
    return "{0}-{1}-{2}{3}p00px" -f $Dataset, $tail, $tag, $Target.ToString("000")
}

function Update-QualityStatus {
    $plan = Import-Csv -LiteralPath $PlanCsv
    $rows = New-Object System.Collections.Generic.List[object]

    foreach ($item in $plan) {
        $dataset = $item.dataset_name
        $images = [regex]::Match($dataset, "-i(\d+)-").Groups[1].Value
        foreach ($targetText in ($item.targets -split " " | Where-Object { $_ })) {
            $target = [int]$targetText
            if ($target -le 100) {
                $expectedMode = "init-pose-triangulate"
            } else {
                $expectedMode = "init-pose-point"
            }

            $variant = Get-VariantName -Dataset $dataset -Target $target
            $dir = Join-Path $OutputRoot (Join-Path "pvl-ba" (Join-Path $dataset (Join-Path "quality" $variant)))
            $required = @("XYZ.txt", "Feature.txt", "dataset_metadata.json", "Cam-$images-.txt", "cal.txt", "noise_metadata.json")
            $missingFiles = @()
            $actualRmse = $null
            $withinTolerance = $null
            $metadataTarget = $null
            $actualMode = $null
            $status = "complete"

            if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
                $status = "missing_dir"
                $missingFiles = $required
            } else {
                foreach ($file in $required) {
                    if (-not (Test-Path -LiteralPath (Join-Path $dir $file) -PathType Leaf)) {
                        $missingFiles += $file
                    }
                }
                if ($missingFiles.Count -gt 0) {
                    $status = "incomplete_files"
                }

                $metaPath = Join-Path $dir "noise_metadata.json"
                if (Test-Path -LiteralPath $metaPath -PathType Leaf) {
                    try {
                        $meta = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json
                        $actualMode = $meta.mode
                        $actualRmse = $meta.actual_rmse_px
                        $withinTolerance = $meta.target_within_tolerance
                        $metadataTarget = $meta.target_rmse_px
                        if ($status -eq "complete") {
                            if (($actualMode -ne $expectedMode) -or ([double]$metadataTarget -ne [double]$target) -or ($withinTolerance -ne $true)) {
                                $status = "metadata_mismatch"
                            }
                        }
                    } catch {
                        if ($status -eq "complete") {
                            $status = "metadata_error"
                        }
                    }
                } elseif ($status -eq "complete") {
                    $status = "incomplete_files"
                }
            }

            $rows.Add([pscustomobject]@{
                dataset_name = $dataset
                target_rmse_px = $target
                expected_mode = $expectedMode
                variant_name = $variant
                status = $status
                missing_files = ($missingFiles -join ";")
                actual_rmse_px = $actualRmse
                target_within_tolerance = $withinTolerance
                metadata_target_rmse_px = $metadataTarget
                path = $dir
            })
        }
    }

    $rows | Sort-Object dataset_name, target_rmse_px | Export-Csv -LiteralPath $DetailCsv -NoTypeInformation -Encoding UTF8
    $summary = $rows | Group-Object dataset_name | ForEach-Object {
        $group = @($_.Group)
        $remaining = @($group | Where-Object { $_.status -ne "complete" })
        [pscustomobject]@{
            dataset_name = $_.Name
            expected_quality_variants = $group.Count
            complete_quality_variants = @($group | Where-Object { $_.status -eq "complete" }).Count
            remaining_quality_variants = $remaining.Count
            remaining_targets = (($remaining | ForEach-Object { "{0}:{1}" -f $_.target_rmse_px, $_.status }) -join " ")
        }
    }
    $summary | Sort-Object @{Expression = "remaining_quality_variants"; Descending = $true}, dataset_name |
        Export-Csv -LiteralPath $SummaryCsv -NoTypeInformation -Encoding UTF8

    return $rows
}

function Invoke-QualityGenerator {
    param(
        [string]$Dataset,
        [string]$Mode,
        [object[]]$Targets,
        [string]$ScaleSolver,
        [int]$SearchSteps
    )

    if ($Targets.Count -eq 0) {
        return
    }

    $inputDir = Join-Path $OutputRoot (Join-Path "pvl-ba" (Join-Path $Dataset "original"))
    $qualityRoot = Join-Path $OutputRoot (Join-Path "pvl-ba" (Join-Path $Dataset "quality"))
    $targetArgs = @($Targets | ForEach-Object { [string]$_ })
    $command = @(
        $Generator,
        "--input-dir", $inputDir,
        "--output-root", $qualityRoot,
        "--seed", [string]$Seed,
        "--mode", $Mode,
        "--prefix", $Dataset,
        "--rotation-weight-deg", "1.0",
        "--translation-weight", "1.0",
        "--point-weight", "1.0",
        "--max-scale", "1.0",
        "--bisection-iterations", "24",
        "--scale-solver", $ScaleSolver,
        "--sample-max-points", "50000",
        "--full-refine-iterations", [string]$FullRefineIterations,
        "--full-refine-tolerance", [string]$Tolerance,
        "--full-search-steps", [string]$SearchSteps,
        "--error-sample-max-points", "100000",
        "--static-file-policy", $StaticFilePolicy,
        "--target-rmse"
    ) + $targetArgs

    Write-Log ("RUN dataset={0} mode={1} solver={2} targets={3}" -f $Dataset, $Mode, $ScaleSolver, ($targetArgs -join ","))
    & python @command 2>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) {
        throw "generator failed with exit code $LASTEXITCODE"
    }
}

Write-Log "refreshing status"
$rows = Update-QualityStatus

while ($true) {
    $createPending = @($rows | Where-Object { $_.status -eq "missing_dir" -or $_.status -eq "incomplete_files" })
    if ($createPending.Count -eq 0) {
        break
    }

    $groups = @($createPending | Group-Object dataset_name, expected_mode | Sort-Object Name)
    foreach ($group in $groups) {
        $items = @($group.Group)
        $dataset = $items[0].dataset_name
        $mode = $items[0].expected_mode
        $targets = @($items | Sort-Object {[int]$_.target_rmse_px} | ForEach-Object { [int]$_.target_rmse_px })

        try {
            Invoke-QualityGenerator -Dataset $dataset -Mode $mode -Targets $targets -ScaleSolver "sampled" -SearchSteps $FullSearchSteps
        } catch {
            Write-Log ("ERROR create pass failed for {0} {1}: {2}" -f $dataset, $mode, $_.Exception.Message)
        }

        Write-Log "refreshing status after create pass"
        $rows = Update-QualityStatus
    }
}

$rows = Update-QualityStatus
if (-not $MissingOnly) {
    $mismatches = @($rows | Where-Object { $_.status -eq "metadata_mismatch" })
    foreach ($item in ($mismatches | Sort-Object dataset_name, {[int]$_.target_rmse_px})) {
        $dataset = $item.dataset_name
        $mode = $item.expected_mode
        $target = [int]$item.target_rmse_px

        try {
            Invoke-QualityGenerator -Dataset $dataset -Mode $mode -Targets @($target) -ScaleSolver "sampled" -SearchSteps $MismatchFullSearchSteps
        } catch {
            Write-Log ("ERROR mismatch pass failed for {0} {1} target={2}: {3}" -f $dataset, $mode, $target, $_.Exception.Message)
            continue
        }

        Write-Log "refreshing status after mismatch pass"
        $rows = Update-QualityStatus

        $stillBad = @($rows | Where-Object {
            $_.dataset_name -eq $dataset -and [int]$_.target_rmse_px -eq $target -and $_.status -ne "complete"
        })
        if ($stillBad.Count -gt 0 -and $RetryExact) {
            try {
                Invoke-QualityGenerator -Dataset $dataset -Mode $mode -Targets @($target) -ScaleSolver "exact" -SearchSteps 0
                Write-Log "refreshing status after exact mismatch pass"
                $rows = Update-QualityStatus
            } catch {
                Write-Log ("ERROR exact mismatch pass failed for {0} {1} target={2}: {3}" -f $dataset, $mode, $target, $_.Exception.Message)
            }
        }
    }
}

$rows = Update-QualityStatus
$counts = $rows | Group-Object status | Sort-Object Count -Descending | ForEach-Object { "{0}={1}" -f $_.Name, $_.Count }
Write-Log ("final status: {0}" -f ($counts -join " "))
Write-Log ("detail csv: {0}" -f $DetailCsv)
Write-Log ("summary csv: {0}" -f $SummaryCsv)
