#!/usr/bin/env pwsh

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv-pcirs-test\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error "Project interpreter was not found: $python"
    exit 1
}

Write-Host "Using project interpreter: $python"
$versionOutput = & $python --version 2>&1
$versionExitCode = $LASTEXITCODE
$versionOutput | ForEach-Object { Write-Host $_ }
if ($versionExitCode -ne 0) {
    Write-Error "Project interpreter check failed with exit code $versionExitCode."
    exit $versionExitCode
}

Write-Host 'Checking installed package requirements...'
$pipOutput = & $python -m pip check 2>&1
$pipExitCode = $LASTEXITCODE
$pipOutput | ForEach-Object { Write-Host $_ }
if ($pipExitCode -ne 0) {
    Write-Error "pip check failed with exit code $pipExitCode."
    exit $pipExitCode
}

Write-Host 'Running the full pytest suite...'
$testOutput = & $python -m pytest -v 2>&1
$testExitCode = $LASTEXITCODE
$testOutput | ForEach-Object { Write-Output $_ }

$summaryLines = @($testOutput | Where-Object { "$_" -match '^=+\s+.*(passed|failed|error|errors|skipped|warnings|deselected).*\s+=+$' })
if ($summaryLines.Count -gt 0) {
    Write-Host "Final test summary: $($summaryLines[-1])"
} else {
    Write-Host 'Final test summary: pytest did not emit a summary line.'
}

if ($testExitCode -ne 0) {
    Write-Error "pytest failed with exit code $testExitCode."
    exit $testExitCode
}

exit 0
