param(
    [Parameter(Mandatory=$true)]
    [string]$InputDir,

    [Parameter(Mandatory=$true)]
    [string]$OutputRoot,

    [Parameter(Mandatory=$true)]
    [double[]]$TargetRmse,

    [int]$Seed = 20260603,
    [string]$Prefix = "problem",
    [switch]$IncludeGcpObservations
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$argsList = @(
    "$ScriptDir\generate_noisy_pvl_ba.py",
    "--input-dir", $InputDir,
    "--output-root", $OutputRoot,
    "--seed", "$Seed",
    "--prefix", $Prefix,
    "--target-rmse"
) + ($TargetRmse | ForEach-Object { "$_" })

if ($IncludeGcpObservations) {
    $argsList += "--include-gcp-observations"
}

python @argsList
