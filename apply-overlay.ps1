[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$TargetRepo,

    [switch]$AllowDifferentBase
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$overlay = Join-Path $PSScriptRoot "overlay"
$target = (Resolve-Path -LiteralPath $TargetRepo).Path
if (-not (Test-Path -LiteralPath (Join-Path $target ".git"))) {
    throw "TargetRepo is not a Git checkout: $target"
}

$baseCommit = (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "BASE_COMMIT.txt")).Trim()
$targetHeadOutput = & git -c "safe.directory=$target" -C $target rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or -not $targetHeadOutput) {
    throw "Unable to read target HEAD: $target"
}

$targetHead = $targetHeadOutput.Trim()
if (-not $AllowDifferentBase -and $targetHead -ne $baseCommit) {
    throw "Target HEAD $targetHead does not match overlay base $baseCommit. Use -AllowDifferentBase only after validating the upgrade."
}

$files = Get-ChildItem -File -Recurse -LiteralPath $overlay
$overlayPrefix = $overlay.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($file in $files) {
    $relative = $file.FullName.Substring($overlayPrefix.Length)
    if ($relative -match '^(config|data|debug|cache)[\\/]') {
        throw "Overlay must not contain runtime data: $relative"
    }

    $destination = Join-Path $target $relative
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
}

Write-Host "Applied $($files.Count) files to $target" -ForegroundColor Green
