param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int[]]$Pids = @(),
    [string[]]$Labels = @(),
    [int]$IntervalSeconds = 60,
    [string]$LogPath = "",
    [int]$MaxSamples = 0
)

$ErrorActionPreference = "Continue"

if (-not $LogPath) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $LogPath = Join-Path $RepoRoot "outputs\release_monitor_$stamp.log"
}

$logDir = Split-Path -Parent $LogPath
if ($logDir) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

function Write-MonitorLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Get-Label {
    param([int]$Index, [int]$PidValue)
    if ($Labels.Count -gt $Index -and $Labels[$Index]) {
        return $Labels[$Index]
    }
    return "pid_$PidValue"
}

function Write-ProcessStatus {
    for ($i = 0; $i -lt $Pids.Count; $i++) {
        $pidValue = $Pids[$i]
        $label = Get-Label -Index $i -PidValue $pidValue
        $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($null -eq $proc) {
            Write-MonitorLog ("process label={0} pid={1} status=exited" -f $label, $pidValue)
        } else {
            Write-MonitorLog (
                "process label={0} pid={1} status=running cpu={2:n2}s ws_mb={3:n1} start={4}" -f
                $label,
                $pidValue,
                $proc.CPU,
                ($proc.WorkingSet64 / 1MB),
                $proc.StartTime.ToString("yyyy-MM-dd HH:mm:ss")
            )
        }
    }
}

function Write-BaColmapStatus {
    $statusCsv = Join-Path $RepoRoot "outputs\ba_colmap_release\ba_colmap_quality_release_status.csv"
    if (Test-Path -LiteralPath $statusCsv -PathType Leaf) {
        try {
            $counts = Import-Csv -LiteralPath $statusCsv |
                Group-Object status |
                Sort-Object Name |
                ForEach-Object { "{0}={1}" -f $_.Name, $_.Count }
            Write-MonitorLog ("ba_colmap_quality_status {0}" -f ($counts -join " "))
        } catch {
            Write-MonitorLog ("ba_colmap_quality_status error={0}" -f $_.Exception.Message)
        }
    }

    $logDir = Join-Path $RepoRoot "outputs\ba_colmap_release\logs"
    if (Test-Path -LiteralPath $logDir -PathType Container) {
        $latest = Get-ChildItem -LiteralPath $logDir -Filter "resume_quality_*.log" |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($latest) {
            $lastLine = Get-Content -LiteralPath $latest.FullName -Tail 1 -ErrorAction SilentlyContinue
            Write-MonitorLog (
                "ba_colmap_latest_log file={0} last_write={1} tail={2}" -f
                $latest.Name,
                $latest.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"),
                $lastLine
            )
        }
    }
}

function Format-Percent {
    param([int]$Done, [int]$Total)
    if ($Total -le 0) {
        return "n/a"
    }
    return ("{0:n2}%" -f (($Done * 100.0) / $Total))
}

function Test-PvlOriginalComplete {
    param([string]$Path)
    return (
        (Test-Path -LiteralPath (Join-Path $Path "cal.txt") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "Feature.txt") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "XYZ.txt") -PathType Leaf) -and
        (@(Get-ChildItem -LiteralPath $Path -Filter "Cam-*.txt" -File -ErrorAction SilentlyContinue).Count -gt 0)
    )
}

function Test-ColmapOriginalComplete {
    param([string]$Path)
    return (
        (Test-Path -LiteralPath (Join-Path $Path "cameras.txt") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "images.txt") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "points3D.txt") -PathType Leaf)
    )
}

function Test-BalOriginalComplete {
    param([string]$Path)
    return (Test-Path -LiteralPath $Path -PathType Leaf)
}

function Count-QualityVariants {
    param([string]$QualityRoot)
    if (-not (Test-Path -LiteralPath $QualityRoot -PathType Container)) {
        return 0
    }
    return @(
        Get-ChildItem -LiteralPath $QualityRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object {
                Test-PvlOriginalComplete -Path $_.FullName -and
                (Test-Path -LiteralPath (Join-Path $_.FullName "noise_metadata.json") -PathType Leaf)
            }
    ).Count
}

function Get-LatestAreaLogTail {
    param([string]$Area)
    $patterns = @("{0}_resume_release_*.log" -f $Area, "{0}_incremental*.log" -f $Area)
    $latest = $null
    foreach ($pattern in $patterns) {
        $candidate = Get-ChildItem -LiteralPath (Join-Path $RepoRoot "outputs") -Filter $pattern -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($candidate -and (($null -eq $latest) -or ($candidate.LastWriteTime -gt $latest.LastWriteTime))) {
            $latest = $candidate
        }
    }
    if (-not $latest) {
        return ""
    }

    $tail = Get-Content -LiteralPath $latest.FullName -Tail 80 -ErrorAction SilentlyContinue
    [array]::Reverse($tail)
    foreach ($line in $tail) {
        if ($line -match "publish originals \[(\d+)\]\s+(.+)$") {
            return ("file={0} stage=publish_originals current_item={1} dataset={2}" -f $latest.Name, $Matches[1], $Matches[2])
        }
        if ($line -match "quality \[(\d+)/(\d+)\]\s+(.+)$") {
            return ("file={0} stage=quality current_item={1}/{2} detail={3}" -f $latest.Name, $Matches[1], $Matches[2], $Matches[3])
        }
        if ($line -match "quality plan add\s+(.+?)\s+base=") {
            return ("file={0} stage=quality_plan dataset={1}" -f $latest.Name, $Matches[1])
        }
        if ($line -match "generate_viewer_index|index.html") {
            return ("file={0} stage=viewer_index tail={1}" -f $latest.Name, $line)
        }
    }
    return ("file={0} tail={1}" -f $latest.Name, ($tail | Select-Object -First 1))
}

function Write-AbsRelStatus {
    foreach ($area in @("abs", "rel")) {
        $release = Join-Path $RepoRoot ("outputs\{0}_release" -f $area)
        $manifest = Join-Path $RepoRoot ("manifests\{0}.csv" -f $area)
        if ($area -eq "abs" -and -not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
            $manifest = Get-ChildItem -LiteralPath (Join-Path $RepoRoot "manifests") -Filter "abs_*.csv" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1 |
                ForEach-Object { $_.FullName }
        }

        $manifestRows = 0
        $manifestData = @()
        if ($manifest -and (Test-Path -LiteralPath $manifest -PathType Leaf)) {
            try {
                $manifestData = @(Import-Csv -LiteralPath $manifest)
                $manifestRows = $manifestData.Count
            } catch {
                $manifestRows = -1
            }
        }

        $pvlDirs = 0
        $pvlComplete = 0
        $colmapComplete = 0
        $balComplete = 0
        $allOriginalsComplete = 0
        $planRows = 0
        $qualityExpected = 0
        $qualityComplete = 0
        $viewers = 0
        $indexExists = 0
        if (Test-Path -LiteralPath $release -PathType Container) {
            $pvlRoot = Join-Path $release "pvl-ba"
            $colmapRoot = Join-Path $release "colmap"
            $balRoot = Join-Path $release "bal"
            $viewerRoot = Join-Path $release "viewers"
            if (Test-Path -LiteralPath $pvlRoot -PathType Container) {
                $pvlDirs = @(Get-ChildItem -Directory -LiteralPath $pvlRoot -ErrorAction SilentlyContinue).Count
            }

            foreach ($row in $manifestData) {
                $name = $row.dataset_name
                if (-not $name) {
                    continue
                }
                $pvlOriginal = Join-Path (Join-Path $pvlRoot $name) "original"
                $colmapOriginal = Join-Path (Join-Path $colmapRoot $name) "original"
                $balOriginal = Join-Path (Join-Path $balRoot $name) "original.bal"
                $hasPvl = Test-PvlOriginalComplete -Path $pvlOriginal
                $hasColmap = Test-ColmapOriginalComplete -Path $colmapOriginal
                $hasBal = Test-BalOriginalComplete -Path $balOriginal
                if ($hasPvl) {
                    $pvlComplete++
                }
                if ($hasColmap) {
                    $colmapComplete++
                }
                if ($hasBal) {
                    $balComplete++
                }
                if ($hasPvl -and $hasColmap -and $hasBal) {
                    $allOriginalsComplete++
                }
            }

            if (Test-Path -LiteralPath $viewerRoot -PathType Container) {
                $viewers = @(Get-ChildItem -File -LiteralPath $viewerRoot -Filter "*.html" -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -ne "index.html" }).Count
                if (Test-Path -LiteralPath (Join-Path $viewerRoot "index.html") -PathType Leaf) {
                    $indexExists = 1
                }
            }

            $planPath = Join-Path $release ("{0}_quality_plan.csv" -f $area)
            if (Test-Path -LiteralPath $planPath -PathType Leaf) {
                try {
                    $planData = @(Import-Csv -LiteralPath $planPath)
                    $planRows = $planData.Count
                    foreach ($planRow in $planData) {
                        $targets = @($planRow.targets -split "\s+" | Where-Object { $_ })
                        $qualityExpected += $targets.Count
                        if ($targets.Count -gt 0 -and $planRow.dataset_name) {
                            $qualityRoot = Join-Path (Join-Path (Join-Path $pvlRoot $planRow.dataset_name) "quality") ""
                            $qualityComplete += [Math]::Min((Count-QualityVariants -QualityRoot $qualityRoot), $targets.Count)
                        }
                    }
                } catch {
                    Write-MonitorLog ("{0}_quality_plan_status error={1}" -f $area, $_.Exception.Message)
                }
            }
        }

        Write-MonitorLog (
            "{0}_release_status manifest_rows={1} pvl_original_dirs={2} viewer_pages={3}" -f
            $area,
            $manifestRows,
            $pvlDirs,
            $viewers
        )
        Write-MonitorLog (
            "{0}_progress originals_pvl={1}/{2}({3}) originals_colmap={4}/{2}({5}) originals_bal={6}/{2}({7}) originals_all={8}/{2}({9}) quality_plan={10}/{2}({11}) quality_variants_plan_scope={12}/{13}({14}) viewers={15}/{2}({16}) index={17}/1({18})" -f
            $area,
            $pvlComplete,
            $manifestRows,
            (Format-Percent -Done $pvlComplete -Total $manifestRows),
            $colmapComplete,
            (Format-Percent -Done $colmapComplete -Total $manifestRows),
            $balComplete,
            (Format-Percent -Done $balComplete -Total $manifestRows),
            $allOriginalsComplete,
            (Format-Percent -Done $allOriginalsComplete -Total $manifestRows),
            $planRows,
            (Format-Percent -Done $planRows -Total $manifestRows),
            $qualityComplete,
            $qualityExpected,
            (Format-Percent -Done $qualityComplete -Total $qualityExpected),
            $viewers,
            (Format-Percent -Done $viewers -Total $manifestRows),
            $indexExists,
            (Format-Percent -Done $indexExists -Total 1)
        )
        $tail = Get-LatestAreaLogTail -Area $area
        if ($tail) {
            Write-MonitorLog ("{0}_active_log {1}" -f $area, $tail)
        }
    }
}

Write-MonitorLog ("monitor_start repo={0} interval_seconds={1} pids={2}" -f $RepoRoot, $IntervalSeconds, ($Pids -join ","))

$sample = 0
while ($true) {
    $sample++
    Write-MonitorLog ("sample={0}" -f $sample)
    Write-ProcessStatus
    Write-BaColmapStatus
    Write-AbsRelStatus

    if ($MaxSamples -gt 0 -and $sample -ge $MaxSamples) {
        Write-MonitorLog "monitor_stop reason=max_samples"
        break
    }

    $running = $false
    foreach ($pidValue in $Pids) {
        if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
            $running = $true
            break
        }
    }
    if ($Pids.Count -gt 0 -and -not $running) {
        Write-MonitorLog "monitor_stop reason=all_pids_exited"
        break
    }

    Start-Sleep -Seconds $IntervalSeconds
}
