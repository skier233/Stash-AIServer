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
            (Test-Path -LiteralPath (Join-Path $current 'config.env') -PathType Leaf -ErrorAction SilentlyContinue) -or
            (Test-Path -LiteralPath (Join-Path $current 'environment.yml') -PathType Leaf -ErrorAction SilentlyContinue)
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

function Test-CondaEnvExists {
    param([string]$EnvName)

    try {
        $lines = Invoke-CondaEntry env list
    } catch {
        return $false
    }

    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }

        if ($trimmed -match '^(?<name>\S+)\s+(?<path>.+)$') {
            $name = $Matches['name']
            $path = $Matches['path']
            if ($name -eq $EnvName -or $path.EndsWith($EnvName)) {
                return $true
            }
        }
    }

    return $false
}

function Import-EnvFile {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $pair = $line -split '=', 2
        if ($pair.Count -ne 2) { return }
        $key = $pair[0].Trim()
        $value = $pair[1].Trim().Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($key, $value)
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw 'conda is required but was not found in PATH.'
}

if (-not $env:CONDA_NO_PLUGINS) { $env:CONDA_NO_PLUGINS = '1' }
if (-not $env:CONDA_DONT_LOAD_PLUGINS) { $env:CONDA_DONT_LOAD_PLUGINS = '1' }
$env:CONDA_SOLVER = 'classic'

$previousConfig = $env:AI_SERVER_CONFIG_FILE
if ($Config) {
    $resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
    $env:AI_SERVER_CONFIG_FILE = $resolvedConfig
}

$configPath = if ($Config) { $Config } else { Join-Path $rootDir 'config.env' }
Import-EnvFile $configPath

$pgDataDir = if ($env:AI_SERVER_PG_DATA_DIR) { (Resolve-Path -LiteralPath $env:AI_SERVER_PG_DATA_DIR).Path } else { Join-Path $rootDir 'data\postgres' }
$pgUser = if ($env:AI_SERVER_DB_USER) { $env:AI_SERVER_DB_USER } else { 'stash_ai_server' }
$pgPassword = if ($env:AI_SERVER_DB_PASSWORD) { $env:AI_SERVER_DB_PASSWORD } else { 'stash_ai_server' }
$pgDatabase = if ($env:AI_SERVER_DB_NAME) { $env:AI_SERVER_DB_NAME } else { 'stash_ai_server' }
$pgPort = if ($env:AI_SERVER_DB_PORT) { [int]$env:AI_SERVER_DB_PORT } else { 5544 }
$pgLog = Join-Path $pgDataDir 'postgres.log'
$postgresServiceScript = Join-Path $PSScriptRoot 'postgres_service.py'
if (-not (Test-Path -LiteralPath $postgresServiceScript)) {
    $candidate = Join-Path $PSScriptRoot 'conda\postgres_service.py'
    if (Test-Path -LiteralPath $candidate) {
        $postgresServiceScript = $candidate
    }
}
if (-not (Test-Path -LiteralPath $postgresServiceScript)) {
    throw "postgres_service.py not found (looked for $postgresServiceScript)"
}

function Invoke-PgService {
    param([string[]]$PgArgs)
    $condaArgs = @('--no-plugins','run','--no-capture-output','-n',$Name,'python',$postgresServiceScript)
    if ($PgArgs) {
        $condaArgs += $PgArgs
    }
    & conda @condaArgs
}

function Start-Postgres {
    Invoke-PgService -PgArgs @('init','--data-dir',$pgDataDir,'--user',$pgUser,'--password',$pgPassword,'--port',$pgPort,'--log-file',$pgLog)
    Invoke-PgService -PgArgs @('start','--data-dir',$pgDataDir,'--port',$pgPort,'--log-file',$pgLog)
    Invoke-PgService -PgArgs @('ensure-db','--data-dir',$pgDataDir,'--user',$pgUser,'--password',$pgPassword,'--database',$pgDatabase,'--port',$pgPort)
}

function Stop-Postgres {
    if (Test-Path -LiteralPath (Join-Path $pgDataDir 'postmaster.pid')) {
        Invoke-PgService -PgArgs @('stop','--data-dir',$pgDataDir,'--port',$pgPort,'--log-file',$pgLog) | Out-Null
    }
}

$cmd = @('run','--no-capture-output','--cwd',$rootDir,'-n',$Name,'python','-m','stash_ai_server.entrypoint')
if ($PassThruArgs) {
    $cmd += $PassThruArgs
}

if (-not (Test-CondaEnvExists -EnvName $Name)) {
    $hint = "Run scripts/install/conda/install.ps1 -Name $Name -EnvironmentFile backend/environment.yml"
    throw "Conda environment '$Name' is missing or invalid. $hint"
}

$pgStarted = $false
try {
    Start-Postgres
    $pgStarted = $true
    Invoke-CondaEntry @cmd
}
finally {
    if ($pgStarted) {
        Stop-Postgres
    }
    if ($Config) {
        if ($previousConfig) {
            $env:AI_SERVER_CONFIG_FILE = $previousConfig
        } else {
            Remove-Item Env:AI_SERVER_CONFIG_FILE -ErrorAction SilentlyContinue
        }
    }
}
