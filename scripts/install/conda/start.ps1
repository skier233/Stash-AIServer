[CmdletBinding()]
param(
    [string]$Name = "stash-ai-server",
    [string]$Config,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PassThruArgs
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

function Invoke-CondaEntry {
    & conda --no-plugins @args
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw 'conda is required but was not found in PATH.'
}

$previousConfig = $env:AI_SERVER_CONFIG_FILE
if ($Config) {
    $resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
    $env:AI_SERVER_CONFIG_FILE = $resolvedConfig
}

$cmd = @('run','--no-capture-output','--cwd',$rootDir,'-n',$Name,'python','-m','stash_ai_server.entrypoint')
if ($PassThruArgs) {
    $cmd += $PassThruArgs
}

try {
    Invoke-CondaEntry @cmd
}
finally {
    if ($Config) {
        if ($previousConfig) {
            $env:AI_SERVER_CONFIG_FILE = $previousConfig
        } else {
            Remove-Item Env:AI_SERVER_CONFIG_FILE -ErrorAction SilentlyContinue
        }
    }
}
