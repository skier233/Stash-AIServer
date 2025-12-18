[CmdletBinding()]
param(
    [string]$Name = "stash-ai-server",
    [string]$EnvironmentFile
)

$ErrorActionPreference = 'Stop'
$rootDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $EnvironmentFile) {
    $EnvironmentFile = Join-Path $rootDir 'environment.yml'
}
if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
    throw "Environment file not found at '$EnvironmentFile'"
}
$EnvironmentFile = (Resolve-Path -LiteralPath $EnvironmentFile).Path

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw 'conda is required but was not found in PATH.'
}

if (-not (conda env list | Select-String -SimpleMatch " $Name ")) {
    throw "Environment '$Name' was not found. Run install.ps1 first."
}

Write-Host "Refreshing dependencies defined in $EnvironmentFile"
& conda env update --name $Name --file $EnvironmentFile --prune

Write-Host 'Forcing pip to download the newest stash-ai-server wheel'
& conda run -n $Name python -m pip install --upgrade --no-cache-dir stash-ai-server

Write-Host "Restart the server with scripts/install/conda/start.ps1 -Name $Name"
