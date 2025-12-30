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
            (Test-Path -LiteralPath (Join-Path $current 'environment.yml') -PathType Leaf -ErrorAction SilentlyContinue) -or
            (Test-Path -LiteralPath (Join-Path $current 'backend\config.env') -PathType Leaf -ErrorAction SilentlyContinue) -or
            (Test-Path -LiteralPath (Join-Path $current 'backend\environment.yml') -PathType Leaf -ErrorAction SilentlyContinue)
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
$backendDir = Join-Path $rootDir 'backend'

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

$configPath = if ($Config) { 
    $Config 
} else { 
    $backendConfig = Join-Path $rootDir 'backend\config.env'
    if (Test-Path -LiteralPath $backendConfig -PathType Leaf -ErrorAction SilentlyContinue) {
        $backendConfig
    } else {
        Join-Path $rootDir 'config.env'
    }
}
Import-EnvFile $configPath

# Set AI_SERVER_PLUGINS_DIR if not already set
if (-not $env:AI_SERVER_PLUGINS_DIR) {
    $pluginsDir = Join-Path $rootDir 'plugins'
    if (Test-Path -LiteralPath $pluginsDir -PathType Container) {
        $env:AI_SERVER_PLUGINS_DIR = (Resolve-Path -LiteralPath $pluginsDir).Path
    }
}

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

if (-not (Test-CondaEnvExists -EnvName $Name)) {
    $hint = "Run scripts/install/conda/install.ps1 -Name $Name -EnvironmentFile backend/environment.yml"
    throw "Conda environment '$Name' is missing or invalid. $hint"
}

# DEV MODE: Use local code with PYTHONPATH and uvicorn reload
$serverHost = if ($env:AI_SERVER_HOST) { $env:AI_SERVER_HOST } else { '0.0.0.0' }
$serverPort = if ($env:AI_SERVER_PORT) { [int]$env:AI_SERVER_PORT } else { 4153 }

Write-Host "!!!!!!!!!!!!!! DEV MODE: Using local code from $backendDir !!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Green
Write-Host "!!!!!!!!!!!!!! Changes will auto-reload !!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Green
Write-Host "!!!!!!!!!!!!!! Server will run on http://${serverHost}:${serverPort} !!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Green

# Set PYTHONPATH to backend directory so it uses local code
$env:PYTHONPATH = $backendDir

$pgStarted = $false
try {
    Start-Postgres
    $pgStarted = $true
    
    # Run uvicorn directly with reload enabled
    $cmd = @('run','--no-capture-output','--cwd',$backendDir,'-n',$Name,'uvicorn','stash_ai_server.main:app','--host',$serverHost,'--port',$serverPort,'--reload')
    if ($PassThruArgs) {
        $cmd += $PassThruArgs
    }
    
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

