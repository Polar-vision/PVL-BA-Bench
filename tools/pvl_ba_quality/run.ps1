param(
    [Parameter(Mandatory=$true)]
    [string]$InputDir,

    [Parameter(Mandatory=$true)]
    [string]$OutputRoot,

    [double[]]$TargetRmse,

    [ValidateSet("main", "stress", "all")]
    [string]$Preset = "main",

    [int]$Seed = 20260603,
    [string]$Prefix = "problem",
    [ValidateSet("init-pose-triangulate", "observation-noise")]
    [string]$Mode = "init-pose-triangulate",
    [double]$RotationWeightDeg = 1.0,
    [double]$TranslationWeight = 1.0,
    [double]$MaxScale = 1.0,
    [int]$BisectionIterations = 24,
    [switch]$IncludeGcpObservations
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$argsList = @(
    "$ScriptDir\generate_noisy_pvl_ba.py",
    "--input-dir", $InputDir,
    "--output-root", $OutputRoot,
    "--seed", "$Seed",
    "--prefix", $Prefix,
    "--mode", $Mode,
    "--rotation-weight-deg", "$RotationWeightDeg",
    "--translation-weight", "$TranslationWeight",
    "--max-scale", "$MaxScale",
    "--bisection-iterations", "$BisectionIterations"
)

if ($TargetRmse -and $TargetRmse.Count -gt 0) {
    $argsList += "--target-rmse"
    $argsList += ($TargetRmse | ForEach-Object { "$_" })
} else {
    $argsList += "--preset"
    $argsList += $Preset
}

if ($IncludeGcpObservations) {
    $argsList += "--include-gcp-observations"
}

python @argsList
