[CmdletBinding()]
param(
    [string]$Name = "stash-ai-server",
    [string]$Config,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PassThruArgs
)

$ErrorActionPreference = 'Stop'
$rootDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path

function Invoke-CondaEntry {
    param([string[]]$Args)
    & conda @Args
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw 'conda is required but was not found in PATH.'
}

$previousConfig = $env:AI_SERVER_CONFIG_FILE
if ($Config) {
    $resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
    $env:AI_SERVER_CONFIG_FILE = $resolvedConfig
}

$cmd = @('run', '--no-capture-output', '--cwd', $rootDir, '-n', $Name, 'python', '-m', 'stash_ai_server.entrypoint')
if ($PassThruArgs) {
    $cmd += $PassThruArgs
}

try {
    Invoke-CondaEntry -Args $cmd
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
