# Run explicitly from PowerShell; this script does not install dependencies,
# start Docker, change firewall rules, or invoke an agent.
[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot '.env'),
    [string]$DataDir = '',
    [string]$Python = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

if (-not [System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile = Join-Path $projectRoot $EnvFile
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw 'Missing local deploy/.env. Copy deploy/.env.example and fill the three required values privately.'
}

# Deliberately not a shell or general dotenv evaluator. Only these keys are read;
# values are literal, with optional matching single/double outer quotes.
$knownKeys = @('N8N_ENCRYPTION_KEY', 'RUNNER_TOKEN', 'WORKFLOW_TOKEN', 'RUNNER_HTTP_PORT')
$values = @{}
$lineNumber = 0
foreach ($line in [System.IO.File]::ReadLines($EnvFile)) {
    $lineNumber++
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith('#')) { continue }
    if ($text -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
        throw "Invalid environment-file syntax at line $lineNumber. Use KEY=value; shell statements are not supported."
    }
    $key = $Matches[1]
    $value = $Matches[2].Trim()
    if ($knownKeys -cnotcontains $key) { continue }
    if ($values.ContainsKey($key)) {
        throw "Duplicate known key at line $lineNumber. Keep one definition per key."
    }
    if ($value.StartsWith('"') -or $value.StartsWith("'")) {
        $quote = $value.Substring(0, 1)
        if ($value.Length -lt 2 -or -not $value.EndsWith($quote)) {
            throw "Unclosed quoted value at line $lineNumber. Multiline values are not supported."
        }
        $value = $value.Substring(1, $value.Length - 2)
    }
    # Compose expands dollar syntax while this parser does not. Reject it in
    # shared keys rather than silently use different tokens in the two services.
    if ($value.Contains('$') -or $value.Contains('`') -or $value.Contains('#') -or
        $value -match '[\r\n\x00]') {
        throw "Unsupported value syntax at line $lineNumber. Use literal single-line values without dollar signs, backticks or hash signs."
    }
    $values[$key] = $value
}
foreach ($key in @('N8N_ENCRYPTION_KEY', 'RUNNER_TOKEN', 'WORKFLOW_TOKEN')) {
    if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($values[$key])) {
        throw "Missing required configuration: $key. Fill it in deploy/.env; do not paste secret values into commands or logs."
    }
}
if ($values.ContainsKey('RUNNER_HTTP_PORT') -and $values['RUNNER_HTTP_PORT'] -ne '8000') {
    throw 'Host workflows use port 8000. Set RUNNER_HTTP_PORT=8000; other ports require a coordinated workflow change.'
}

$pythonCommand = Get-Command -Name $Python -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $pythonCommand) {
    throw 'Python executable not found. Install Python >=3.11 and application dependencies, or pass -Python with its executable path.'
}
if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $projectRoot '.local/runner'
} elseif (-not [System.IO.Path]::IsPathRooted($DataDir)) {
    $DataDir = Join-Path $projectRoot $DataDir
}
$DataDir = [System.IO.Path]::GetFullPath($DataDir)

# Pass secrets only through the child environment, never command arguments.
# Preserve the caller's environment when the foreground server exits.
$childEnvironment = @{
    RUNNER_TOKEN = $values['RUNNER_TOKEN']
    WORKFLOW_TOKEN = $values['WORKFLOW_TOKEN']
    DATA_DIR = $DataDir
    N8N_WEBHOOK_BASE = 'http://127.0.0.1:5678/webhook'
}
$previous = @{}
try {
    foreach ($key in $childEnvironment.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $childEnvironment[$key], 'Process')
    }
    Push-Location -LiteralPath $projectRoot
    try {
        Write-Host 'Starting host Runner on 0.0.0.0:8000; open http://127.0.0.1:8000/#runner locally. Stop with Ctrl+C.'
        & $pythonCommand.Source -m uvicorn agentautomation.app:app --app-dir (Join-Path $projectRoot 'src') --host 0.0.0.0 --port 8000
        if ($LASTEXITCODE -ne 0) {
            throw 'Runner exited unsuccessfully. Verify dependencies, port availability and the local application log.'
        }
    } finally {
        Pop-Location
    }
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
    $values.Clear()
    $childEnvironment.Clear()
}
