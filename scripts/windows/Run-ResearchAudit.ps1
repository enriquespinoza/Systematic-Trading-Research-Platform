#Requires -Version 5.1
<# Read-only GitHub audit collector; invoke from a scheduled Windows user task.
   Needs Python 3.11+, gh CLI read access, and a private, persistent state directory.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepoPath,
    [Parameter(Mandatory=$true)][string]$StateDirectory,
    [Parameter(Mandatory=$true)][string]$BackupDirectory,
    [string]$PythonExecutable = 'python',
    [string]$GitHubCLI = 'gh'
)
$ErrorActionPreference = 'Stop'
$env:GH_PROMPT_DISABLED = '1'
$root = (Resolve-Path -LiteralPath $RepoPath).Path
$state = [IO.Path]::GetFullPath($StateDirectory)
$backup = [IO.Path]::GetFullPath($BackupDirectory)
if ($backup.TrimEnd('\') -eq $state.TrimEnd('\') -or $backup.StartsWith($state.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'BackupDirectory must be outside StateDirectory.'
}
$required = Join-Path $root 'audit_registry\__main__.py'
if (-not (Test-Path -LiteralPath $required)) { throw "Audit registry package not found: $required" }
$config = Join-Path $state 'active-engines.json'
if (-not (Test-Path -LiteralPath $config)) { throw 'Missing active-engines.json: run Install-ResearchAuditTask.ps1 first.' }
foreach ($folder in @($state, $backup, (Join-Path $state 'reports'), (Join-Path $state 'logs'))) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}
$lockPath = Join-Path $state 'audit-run.lock'
try {
    $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch [IO.IOException] {
    throw 'Audit collector already running (lock held).'
}
$before = Get-Location
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$log = Join-Path $state ("logs\run-$stamp.log")
try {
    Set-Location -LiteralPath $root
    & $GitHubCLI auth status -h github.com *> $null
    if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI is not authenticated for this Windows user. Run gh auth login interactively.' }
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $registry = Join-Path $state 'registry'
    $reports = Join-Path $state 'reports'
    $md = Join-Path $reports ("audit-delta-$stamp.md")
    $json = Join-Path $reports ("audit-delta-$stamp.json")

    # Fail closed: if collection fails, do not advance the reporting cursor.
    & $PythonExecutable -m audit_registry --registry-dir $registry --engine-config $config collect --per-engine 30 2>&1 | Out-File -FilePath $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "Collector failed; see private log $log" }
    & $PythonExecutable -m audit_registry --registry-dir $registry --engine-config $config report --markdown $md --json $json --acknowledge 2>&1 | Out-File -FilePath $log -Append -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "Report generation failed; see private log $log" }

    $report = Get-Content -LiteralPath $json -Raw | ConvertFrom-Json
    if (@($report.changed_findings | Where-Object { $null -ne $_ }).Count -gt 0) {
        Copy-Item -LiteralPath $md -Destination (Join-Path $reports 'latest-exceptions.md') -Force
        Copy-Item -LiteralPath $json -Destination (Join-Path $reports 'latest-exceptions.json') -Force
    }

    # Backup is made after both Python processes exit and close SQLite.
    $archive = Join-Path $backup ("registry-$stamp.zip")
    Compress-Archive -LiteralPath $registry -DestinationPath $archive -CompressionLevel Optimal -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $archive)) { throw 'Backup was not created.' }
    Write-Output "Audit collected and reported. Changed findings: $(@($report.changed_findings | Where-Object { $null -ne $_ }).Count). Backup: $archive"
} finally {
    Set-Location $before
    $lock.Dispose()
}
