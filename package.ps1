# Builds the release zip: the mod, the planner app's source, and the scripts that run them.
# Usage: .\package.ps1 v0.1.0
param(
    [Parameter(Mandatory = $true)][string]$Version
)

$name = "ShipsAtSeaLogistics-$Version"
$dist = Join-Path $PSScriptRoot "dist"
$stage = Join-Path $dist $name
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $stage "app\saslogi"), (Join-Path $stage "mod") | Out-Null

Copy-Item (Join-Path $PSScriptRoot "mod\SasLogistics") (Join-Path $stage "mod\SasLogistics") -Recurse
Copy-Item (Join-Path $PSScriptRoot "app\saslogi\*.py") (Join-Path $stage "app\saslogi")
Copy-Item (Join-Path $PSScriptRoot "app\icon.ico"), (Join-Path $PSScriptRoot "app\requirements.txt") (Join-Path $stage "app")
foreach ($file in "run.ps1", "install-shortcut.ps1", "deploy.ps1", "README.md", "LICENSE") {
    Copy-Item (Join-Path $PSScriptRoot $file) $stage
}

$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip
Remove-Item $stage -Recurse -Force
"Built $zip"
