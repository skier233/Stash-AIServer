[CmdletBinding()]
param(
    [string]$ComposeFile,
    [string]$Service = 'backend_prod',
    [switch]$FollowLogs,
    [switch]$NoDetach
)

$ErrorActionPreference = 'Stop'
$rootDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
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
        # continue to docker-compose fallback
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
    param([string[]]$Args)
    & $composeCommand @composePrefix -f $ComposeFile @Args
}

Write-Host "Pulling latest image for $Service"
Invoke-Compose -Args @('pull', $Service)

$upArgs = @('up')
if (-not $NoDetach) {
    $upArgs += '-d'
}
if ($Service) {
    $upArgs += $Service
}

Write-Host "Restarting $Service"
Invoke-Compose -Args $upArgs

if ($FollowLogs) {
    Write-Host ''
    Write-Host 'Tailing logs (Ctrl+C to stop)'
    Invoke-Compose -Args @('logs','-f',$Service)
}
