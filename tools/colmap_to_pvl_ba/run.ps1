param(
    [string]$InputDir = "..\COLMAP_TEXT_MODEL",
    [string]$OutputDir = "..\PVL_BA_OUTPUT",
    [ValidateSet("id_minus_one", "row")]
    [string]$ImageIndex = "id_minus_one"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-ProjectPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $scriptDir $PathValue
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python -or $python.Source -like "*\WindowsApps\python.exe") {
    $candidate = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $pythonExe = $candidate
    } else {
        throw "Python was not found. Install Python 3.12 or put python.exe on PATH."
    }
} else {
    $pythonExe = $python.Source
}

& $pythonExe (Join-Path $scriptDir "colmap_to_pvl_ba.py") `
    --input (Resolve-ProjectPath $InputDir) `
    --output (Resolve-ProjectPath $OutputDir) `
    --image-index $ImageIndex

