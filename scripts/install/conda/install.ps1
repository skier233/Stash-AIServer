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
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
    throw 'Unable to locate project root. Set STASH_AI_ROOT environment variable.'
}

function Resolve-EnvFile {
    param([string]$Root, [string]$EnvFile)

    if ($EnvFile) {
        if (-not (Test-Path -LiteralPath $EnvFile)) { throw "Environment file not found at '$EnvFile'" }
        return (Resolve-Path -LiteralPath $EnvFile).Path
    }

    $candidates = @(
        (Join-Path $Root 'environment.yml'),
        (Join-Path $Root 'backend\environment.yml')
    )

    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) {
            return (Resolve-Path -LiteralPath $path).Path
        }
    }

    throw "Environment file not found. Pass -EnvironmentFile to specify one."
}

function Ensure-Conda {
    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        throw 'conda is required but was not found in PATH.'
    }
}

function Get-EnvExists {
    param([string]$EnvName)
    try {
        $lines = conda env list
    } catch {
        return $false
    }
    return $lines -match "^$EnvName\s"
}

$rootDir = Get-StashRootDir -StartPath ((Resolve-Path -LiteralPath $PSScriptRoot).Path)
$envFile = Resolve-EnvFile -Root $rootDir -EnvFile $EnvironmentFile

Ensure-Conda
Write-Host "Using environment file $envFile"

$envExists = Get-EnvExists -EnvName $Name
if ($envExists) {
    Write-Host "Updating existing environment '$Name'"
    conda env update --name $Name --file $envFile --prune
} else {
    Write-Host "Creating environment '$Name'"
    conda env create --name $Name --file $envFile
}

Write-Host ''
Write-Host "Environment '$Name' is ready."
Write-Host "Start the server with scripts/conda/start.ps1 -Name $Name (scripts/install/conda/start.ps1 in source)"
