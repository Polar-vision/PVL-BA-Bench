param(
    [Parameter(Mandatory=$true)]
    [string]$InputRoot,

    [Parameter(Mandatory=$true)]
    [string]$OutputHtml,

    [string]$ReferenceDir = "",

    [int]$MaxPoints = 100000,
    [int]$CameraStride = 1,
    [double]$StressThreshold = 200.0
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @(
    "$ScriptDir\generate_quality_viewer.py",
    "--input-root", $InputRoot,
    "--output", $OutputHtml,
    "--max-points", [string]$MaxPoints,
    "--camera-stride", [string]$CameraStride,
    "--stress-threshold", [string]$StressThreshold
)
if ($ReferenceDir -ne "") {
    $ArgsList += @("--reference-dir", $ReferenceDir)
}
python @ArgsList
