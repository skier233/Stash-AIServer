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

$script:RootDir = Get-StashRootDir -StartPath ((Resolve-Path -LiteralPath $PSScriptRoot).Path)
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

function Invoke-Conda {
    param([string[]]$Args)
    & conda --no-plugins @Args
}

if (-not (Test-Command 'conda')) {
    throw 'conda is required but was not found in PATH.'
}

Write-Host "Using environment file $EnvironmentFile"

$envExists = (Invoke-Conda env list | Select-String -SimpleMatch " $Name ")
if ($envExists) {
    Write-Host "Updating existing environment '$Name'"
    Invoke-Conda env update --name $Name --file $EnvironmentFile --prune
} else {
    Write-Host "Creating environment '$Name'"
    Invoke-Conda env create --name $Name --file $EnvironmentFile
}

Write-Host 'Installing latest stash-ai-server from PyPI'
Invoke-Conda run -n $Name python -m pip install --upgrade --no-cache-dir stash-ai-server

Write-Host ''
Write-Host "Environment '$Name' is ready."
Write-Host "Start the server with scripts/conda/start.ps1 -Name $Name (scripts/install/conda/start.ps1 in source)"
