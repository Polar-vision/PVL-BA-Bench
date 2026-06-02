param(
    [string]$InputXml = "..\REL_AT\AT.xml",
    [string]$OutputBal = "..\problem.bal",
    [ValidateSet("normalized", "pixel")]
    [string]$Mode = "normalized"
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

& $pythonExe (Join-Path $scriptDir "atxml_to_bal.py") `
    --input (Resolve-ProjectPath $InputXml) `
    --output (Resolve-ProjectPath $OutputBal) `
    --mode $Mode

