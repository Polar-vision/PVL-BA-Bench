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
    [ValidateSet("sampled", "exact")]
    [string]$ScaleSolver = "sampled",
    [int]$SampleMaxPoints = 50000,
    [int]$FullRefineIterations = 12,
    [double]$FullRefineTolerance = 0.02,
    [int]$FullSearchSteps = 0,
    [string[]]$PoseScaleOverride = @(),
    [int]$ErrorSampleMaxPoints = 100000,
    [ValidateSet("hardlink", "copy")]
    [string]$StaticFilePolicy = "hardlink",
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
    "--bisection-iterations", "$BisectionIterations",
    "--scale-solver", $ScaleSolver,
    "--sample-max-points", "$SampleMaxPoints",
    "--full-refine-iterations", "$FullRefineIterations",
    "--full-refine-tolerance", "$FullRefineTolerance",
    "--full-search-steps", "$FullSearchSteps",
    "--error-sample-max-points", "$ErrorSampleMaxPoints",
    "--static-file-policy", $StaticFilePolicy
)

foreach ($override in $PoseScaleOverride) {
    $argsList += @("--pose-scale-override", $override)
}

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
