[CmdletBinding()]
param(
    [string]$Name = "stash-ai-server",
    [string]$EnvironmentFile
)

$ErrorActionPreference = 'Stop'

function Get-StashRootDir {
    param([string]$StartPath)

    if ($env:STASH_AI_ROOT) {
        return (Resolve-Path -LiteralPath $env:STASH_AI_ROOT).Path
    }

    $current = $StartPath
    while ($true) {
        $hasMarker = (
            (Test-Path -LiteralPath (Join-Path $current 'docker-compose.yml') -PathType Leaf -ErrorAction SilentlyContinue) -or
            (Test-Path -LiteralPath (Join-Path $current 'config.env') -PathType Leaf -ErrorAction SilentlyContinue) -or
            (Test-Path -LiteralPath (Join-Path $current 'environment.yml') -PathType Leaf -ErrorAction SilentlyContinue) -or
            (Test-Path -LiteralPath (Join-Path $current 'backend') -PathType Container -ErrorAction SilentlyContinue)
        )
        if ($hasMarker) {
            return $current
        }
        $parent = Split-Path -Path $current -Parent
        if (-not $parent -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
    throw 'Unable to locate project root. Set STASH_AI_ROOT environment variable.'
}

$rootDir = Get-StashRootDir -StartPath ((Resolve-Path -LiteralPath $PSScriptRoot).Path)
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

function Invoke-Conda {
    param([string[]]$Args)
    & conda --no-plugins @Args
}

if (-not (Invoke-Conda env list | Select-String -SimpleMatch " $Name ")) {
    throw "Environment '$Name' was not found. Run install.ps1 first."
}

Write-Host "Refreshing dependencies defined in $EnvironmentFile"
Invoke-Conda env update --name $Name --file $EnvironmentFile --prune

Write-Host 'Forcing pip to download the newest stash-ai-server wheel'
Invoke-Conda run -n $Name python -m pip install --upgrade --no-cache-dir stash-ai-server

Write-Host "Restart the server with scripts/conda/start.ps1 -Name $Name (scripts/install/conda/start.ps1 in source)"
