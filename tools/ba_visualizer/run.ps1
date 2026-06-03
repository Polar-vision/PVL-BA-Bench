param(
    [Parameter(Mandatory=$true)]
    [string]$InputDir,

    [Parameter(Mandatory=$true)]
    [string]$OutputHtml,

    [int]$MaxPoints = 100000,
    [int]$CameraStride = 1
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$ScriptDir\generate_viewer.py" --input-dir $InputDir --output $OutputHtml --max-points $MaxPoints --camera-stride $CameraStride
