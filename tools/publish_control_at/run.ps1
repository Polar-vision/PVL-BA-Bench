param(
    [string]$Manifest = "manifests\control_at_20.csv",

    [Parameter(Mandatory=$true)]
    [string]$OutputRoot,

    [string[]]$Formats = @("pvl-ba"),

    [switch]$Quality,

    [ValidateSet("main", "stress", "all")]
    [string]$QualityPreset = "all",

    [ValidateSet("init-pose-triangulate", "observation-noise")]
    [string]$QualityMode = "init-pose-triangulate",

    [int]$QualitySeed = 20260603,

    [double]$RotationWeightDeg = 1.0,

    [double]$TranslationWeight = 1.0,

    [double]$MaxScale = 1.0,

    [int]$BisectionIterations = 24,

    [ValidateSet("sampled", "exact")]
    [string]$ScaleSolver = "sampled",

    [int]$SampleMaxPoints = 50000,

    [int]$FullRefineIterations = 12,

    [double]$FullRefineTolerance = 0.02,

    [int]$FullSearchSteps = 12,

    [int]$ErrorSampleMaxPoints = 100000,

    [ValidateSet("hardlink", "copy")]
    [string]$StaticFilePolicy = "hardlink",

    [switch]$IncludeGcpObservations,

    [switch]$Viewers,

    [string]$ViewerCameraStride = "auto",

    [int]$ViewerTargetFrustums = 5000,

    [int]$ViewerMaxPoints = 100000,

    [switch]$Dashboard,

    [int]$Limit = 0,

    [string[]]$Only = @(),

    [switch]$SkipExisting,

    [switch]$DryRun
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @(
    "$ScriptDir\publish_control_at.py",
    "--manifest", $Manifest,
    "--output-root", $OutputRoot,
    "--formats"
) + $Formats

if ($Quality) {
    $ArgsList += @(
        "--quality",
        "--quality-preset", $QualityPreset,
        "--quality-mode", $QualityMode,
        "--quality-seed", [string]$QualitySeed,
        "--rotation-weight-deg", [string]$RotationWeightDeg,
        "--translation-weight", [string]$TranslationWeight,
        "--max-scale", [string]$MaxScale,
        "--bisection-iterations", [string]$BisectionIterations,
        "--scale-solver", $ScaleSolver,
        "--sample-max-points", [string]$SampleMaxPoints,
        "--full-refine-iterations", [string]$FullRefineIterations,
        "--full-refine-tolerance", [string]$FullRefineTolerance,
        "--full-search-steps", [string]$FullSearchSteps,
        "--error-sample-max-points", [string]$ErrorSampleMaxPoints,
        "--static-file-policy", $StaticFilePolicy
    )
    if ($IncludeGcpObservations) {
        $ArgsList += "--include-gcp-observations"
    }
}
if ($Viewers) {
    $ArgsList += @(
        "--viewers",
        "--viewer-camera-stride", $ViewerCameraStride,
        "--viewer-target-frustums", [string]$ViewerTargetFrustums,
        "--viewer-max-points", [string]$ViewerMaxPoints
    )
}
if ($Dashboard) {
    $ArgsList += "--dashboard"
}
if ($Limit -gt 0) {
    $ArgsList += @("--limit", [string]$Limit)
}
if ($Only.Count -gt 0) {
    $ArgsList += "--only"
    $ArgsList += $Only
}
if ($SkipExisting) {
    $ArgsList += "--skip-existing"
}
if ($DryRun) {
    $ArgsList += "--dry-run"
}

python @ArgsList
