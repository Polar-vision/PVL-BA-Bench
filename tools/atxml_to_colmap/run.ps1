param(
    [string]$InputXml = "..\REL_AT\AT.xml",
    [string]$OutputDir = "..\COLMAP_TEXT_MODEL",
    [ValidateSet("OPENCV", "FULL_OPENCV")]
    [string]$CameraModel = "OPENCV"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Get-Command python -ErrorAction SilentlyContinue

function Resolve-ProjectPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $scriptDir $PathValue
}

if (-not $python -or $python.Source -like "*\WindowsApps\python.exe") {
    $candidate = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $pythonExe = $candidate
    } else {
        throw "Python was not found. Install Python 3.12 or pass a working python.exe through PATH."
    }
} else {
    $pythonExe = $python.Source
}

& $pythonExe (Join-Path $scriptDir "atxml_to_colmap.py") `
    --input (Resolve-ProjectPath $InputXml) `
    --output (Resolve-ProjectPath $OutputDir) `
    --camera-model $CameraModel
