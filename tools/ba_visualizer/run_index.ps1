param(
    [Parameter(Mandatory=$true)]
    [string]$ViewerDir,

    [string]$OutputHtml = "",

    [string]$Manifest = "",

    [string]$Title = "PVL-BA Control AT Viewers"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $OutputHtml) {
    $OutputHtml = Join-Path $ViewerDir "index.html"
}

$ArgsList = @(
    "$ScriptDir\generate_viewer_index.py",
    "--viewer-dir", $ViewerDir,
    "--output", $OutputHtml,
    "--title", $Title
)

if ($Manifest) {
    $ArgsList += @("--manifest", $Manifest)
}

python @ArgsList
