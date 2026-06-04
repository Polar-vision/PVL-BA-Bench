param(
    [Parameter(Mandatory=$true)]
    [string]$InputRoot,

    [Parameter(Mandatory=$true)]
    [string]$OutputHtml,

    [int]$MaxPoints = 100000,
    [int]$CameraStride = 1,
    [double]$StressThreshold = 200.0
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$ScriptDir\generate_quality_viewer.py" --input-root $InputRoot --output $OutputHtml --max-points $MaxPoints --camera-stride $CameraStride --stress-threshold $StressThreshold
