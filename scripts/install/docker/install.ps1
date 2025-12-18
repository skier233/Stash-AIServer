[CmdletBinding()]
param(
    [string]$ComposeFile,
    [string]$StashRoot
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
if (-not $ComposeFile) {
    $ComposeFile = Join-Path $rootDir 'docker-compose.yml'
}
if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "docker-compose file not found at '$ComposeFile'"
}
$ComposeFile = (Resolve-Path -LiteralPath $ComposeFile).Path

$placeholder = '/path/to/your/stash_root_folder'
$composeText = Get-Content -LiteralPath $ComposeFile -Raw
$placeholderPresent = $composeText.Contains($placeholder)
if ($placeholderPresent) {
    if (-not $StashRoot) {
        throw 'The compose file still contains the placeholder path. Re-run with -StashRoot <path>.'
    }
    $resolvedStashRoot = (Resolve-Path -LiteralPath $StashRoot).Path
    $composeText = $composeText.Replace($placeholder, $resolvedStashRoot)
    Set-Content -LiteralPath $ComposeFile -Value $composeText
    Write-Host "Updated docker-compose.yml to mount $resolvedStashRoot"
}

New-Item -ItemType Directory -Path (Join-Path $rootDir 'data') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $rootDir 'plugins') -Force | Out-Null

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        docker compose version *> $null
        $composeCommand = 'docker'
        $composePrefix = @('compose')
    } catch {
        # fall back to docker-compose
    }
}
if (-not $composeCommand -and (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    $composeCommand = 'docker-compose'
    $composePrefix = @()
}
if (-not $composeCommand) {
    throw 'docker compose (or docker-compose) is required.'
}

function Invoke-Compose {
    & $composeCommand @composePrefix -f $ComposeFile @args
}

Write-Host 'Pulling latest ghcr.io stash-ai-server image'
Invoke-Compose 'pull' 'backend_prod'

Write-Host ''
Write-Host 'Docker install ready.'
Write-Host 'Use scripts/docker/start.ps1 to launch the container (scripts/install/docker/start.ps1 in source).'
