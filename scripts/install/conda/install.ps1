[CmdletBinding()]
param(
    [string]$Name = "stash-ai-server",
    [string]$EnvironmentFile
)

$ErrorActionPreference = 'Stop'
$script:RootDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $EnvironmentFile) {
    $EnvironmentFile = Join-Path $script:RootDir 'environment.yml'
}
if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
    throw "Environment file not found at '$EnvironmentFile'"
}
$EnvironmentFile = (Resolve-Path -LiteralPath $EnvironmentFile).Path

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command 'conda')) {
    throw 'conda is required but was not found in PATH.'
}

Write-Host "Using environment file $EnvironmentFile"

$envExists = (conda env list | Select-String -SimpleMatch " $Name ")
if ($envExists) {
    Write-Host "Updating existing environment '$Name'"
    & conda env update --name $Name --file $EnvironmentFile --prune
} else {
    Write-Host "Creating environment '$Name'"
    & conda env create --name $Name --file $EnvironmentFile
}

Write-Host 'Installing latest stash-ai-server from PyPI'
& conda run -n $Name python -m pip install --upgrade --no-cache-dir stash-ai-server

Write-Host ''
Write-Host "Environment '$Name' is ready."
Write-Host "Start the server with scripts/install/conda/start.ps1 -Name $Name"
