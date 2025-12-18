[CmdletBinding()]
param(
    [string]$ComposeFile,
    [string]$Service = 'backend_prod',
    [switch]$FollowLogs,
    [switch]$NoDetach
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

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        docker compose version *> $null
        $composeCommand = 'docker'
        $composePrefix = @('compose')
    } catch {
        # Ignore and try docker-compose
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

$upArgs = @('up')
if (-not $NoDetach) {
    $upArgs += '-d'
}
if ($Service) {
    $upArgs += $Service
}

Write-Host "Starting $Service via docker compose"
Invoke-Compose @upArgs

if ($FollowLogs) {
    Write-Host ''
    Write-Host 'Tailing logs (Ctrl+C to stop)'
    Invoke-Compose 'logs' '-f' $Service
}
