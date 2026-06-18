param(
    [Parameter(Mandatory=$true)][string]$LocalRoot,
    [Parameter(Mandatory=$true)][string]$Bucket,
    [Parameter(Mandatory=$true)][string]$EndpointUrl,
    [string]$Profile = "r2",
    [string]$RemotePrefix = "viewers/rel-problem-",
    [string]$ExcludeName = "index.html",
    [int]$UploadPid = 0,
    [string]$UploadStdout = "",
    [string]$UploadStderr = "",
    [Parameter(Mandatory=$true)][string]$LogPath,
    [int]$IntervalSeconds = 60,
    [int]$MaxSamples = 0
)

$ErrorActionPreference = "Continue"

$logDir = Split-Path -Parent $LogPath
if ($logDir) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

function Write-MonitorLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Format-Bytes {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) {
        return ("{0:n2} GB" -f ($Bytes / 1GB))
    }
    if ($Bytes -ge 1MB) {
        return ("{0:n2} MB" -f ($Bytes / 1MB))
    }
    return ("{0:n0} B" -f $Bytes)
}

function Format-Percent {
    param([double]$Done, [double]$Total)
    if ($Total -le 0) {
        return "n/a"
    }
    return ("{0:n2}%" -f (($Done * 100.0) / $Total))
}

function Get-LocalUploadStats {
    $files = @(
        Get-ChildItem -LiteralPath $LocalRoot -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne $ExcludeName }
    )
    $bytes = ($files | Measure-Object Length -Sum).Sum
    if ($null -eq $bytes) {
        $bytes = 0
    }
    return [PSCustomObject]@{
        Count = $files.Count
        Bytes = [double]$bytes
    }
}

function Get-RemoteStats {
    $uri = "s3://$Bucket/$RemotePrefix"
    $output = & python -m awscli s3 ls $uri --recursive --summarize --endpoint-url $EndpointUrl --profile $Profile 2>&1
    $count = 0
    $bytes = 0
    $errorLines = @()
    foreach ($line in $output) {
        if ($line -match "Total Objects:\s+(\d+)") {
            $count = [int]$Matches[1]
        } elseif ($line -match "Total Size:\s+(\d+)") {
            $bytes = [double]$Matches[1]
        } elseif ($line -match "error|failed|An error occurred|Traceback") {
            $errorLines += $line
        }
    }
    return [PSCustomObject]@{
        Count = $count
        Bytes = $bytes
        Error = ($errorLines -join " | ")
    }
}

function Get-UploadTail {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $tail = Get-Content -LiteralPath $Path -Tail 5 -ErrorAction SilentlyContinue
    return (($tail | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join " || ")
}

$localStats = Get-LocalUploadStats
Write-MonitorLog (
    "monitor_start bucket={0} prefix={1} local_root={2} local_files={3} local_size={4} upload_pid={5} interval_seconds={6}" -f
    $Bucket,
    $RemotePrefix,
    $LocalRoot,
    $localStats.Count,
    (Format-Bytes -Bytes $localStats.Bytes),
    $UploadPid,
    $IntervalSeconds
)

$sample = 0
while ($true) {
    $sample++
    $proc = $null
    if ($UploadPid -gt 0) {
        $proc = Get-Process -Id $UploadPid -ErrorAction SilentlyContinue
    }
    $remote = Get-RemoteStats
    $countPct = Format-Percent -Done $remote.Count -Total $localStats.Count
    $bytePct = Format-Percent -Done $remote.Bytes -Total $localStats.Bytes
    $stdoutTail = Get-UploadTail -Path $UploadStdout
    $stderrTail = Get-UploadTail -Path $UploadStderr

    $status = "not_tracked"
    $cpu = "n/a"
    $wsMb = "n/a"
    if ($UploadPid -gt 0) {
        if ($null -eq $proc) {
            $status = "exited"
        } else {
            $status = "running"
            $cpu = ("{0:n2}" -f $proc.CPU)
            $wsMb = ("{0:n1}" -f ($proc.WorkingSet64 / 1MB))
        }
    }

    Write-MonitorLog (
        "sample={0} upload_status={1} cpu_s={2} ws_mb={3} remote_files={4}/{5}({6}) remote_size={7}/{8}({9})" -f
        $sample,
        $status,
        $cpu,
        $wsMb,
        $remote.Count,
        $localStats.Count,
        $countPct,
        (Format-Bytes -Bytes $remote.Bytes),
        (Format-Bytes -Bytes $localStats.Bytes),
        $bytePct
    )
    if ($remote.Error) {
        Write-MonitorLog ("remote_list_error {0}" -f $remote.Error)
    }
    if ($stdoutTail) {
        Write-MonitorLog ("stdout_tail {0}" -f $stdoutTail)
    }
    if ($stderrTail) {
        Write-MonitorLog ("stderr_tail {0}" -f $stderrTail)
    }

    if ($MaxSamples -gt 0 -and $sample -ge $MaxSamples) {
        Write-MonitorLog "monitor_stop reason=max_samples"
        break
    }
    if ($UploadPid -gt 0 -and $null -eq $proc) {
        Write-MonitorLog "monitor_stop reason=upload_process_exited"
        break
    }

    Start-Sleep -Seconds $IntervalSeconds
}
