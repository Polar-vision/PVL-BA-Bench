param(
    [string]$Manifest = "manifests\control_at_20.csv",

    [Parameter(Mandatory=$true)]
    [string]$OutputRoot,

    [string[]]$Formats = @("pvl-ba"),

    [switch]$Quality,

    [ValidateSet("main", "stress", "all")]
    [string]$QualityPreset = "all",

    [switch]$Viewers,

    [switch]$Dashboard,

    [int]$Limit = 0,

    [string[]]$Only = @(),

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
    $ArgsList += @("--quality", "--quality-preset", $QualityPreset)
}
if ($Viewers) {
    $ArgsList += "--viewers"
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
if ($DryRun) {
    $ArgsList += "--dry-run"
}

python @ArgsList
