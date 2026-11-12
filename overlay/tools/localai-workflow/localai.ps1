[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("doctor", "debug", "test", "test-python", "test-dotnet", "build-gui", "verify", "run-ui")]
    [string]$Command = "doctor",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$CommandArguments = @(),

    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Debug",

    [string]$PackageDir = $(if ($env:MAA_LOCALAI_PACKAGE_DIR) { $env:MAA_LOCALAI_PACKAGE_DIR } else { "D:\work\CodexOutputs\MAA-LocalAI" }),

    [string]$Dotnet = $(if ($env:MAA_LOCALAI_DOTNET) { $env:MAA_LOCALAI_DOTNET } else { "D:\work\maa-deps\dotnet\dotnet.exe" }),

    [string]$Python = $(if ($env:MAA_LOCALAI_PYTHON) { $env:MAA_LOCALAI_PYTHON } else { "D:\work\maa-deps\python-autopush\Scripts\python.exe" }),

    [string]$OutputPath = "",

    [switch]$SkipPython,
    [switch]$SkipDotnet,
    [switch]$SkipEndpoint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$autoPushRoot = Join-Path $repoRoot "tools\auto-push"
$wpfProject = Join-Path $repoRoot "src\MaaWpfGui\MaaWpfGui.csproj"
$testProject = Join-Path $repoRoot "src\MaaWpfGui.LocalAi.Tests\MaaWpfGui.LocalAi.Tests.csproj"
$verifyRoot = Join-Path $repoRoot "build\workflow\verify"
$script:failures = 0
$script:warnings = 0

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Result {
    param(
        [ValidateSet("OK", "WARN", "FAIL")]
        [string]$Level,
        [string]$Message
    )

    $color = switch ($Level) {
        "OK" { "Green" }
        "WARN" { "Yellow" }
        "FAIL" { "Red" }
    }
    Write-Host ("[{0}] {1}" -f $Level, $Message) -ForegroundColor $color
    if ($Level -eq "WARN") {
        $script:warnings++
    }
    elseif ($Level -eq "FAIL") {
        $script:failures++
    }
}

function Test-RequiredFile {
    param(
        [string]$Path,
        [string]$Label
    )

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Write-Result OK "$Label`: $Path"
        return $true
    }

    Write-Result FAIL "$Label missing: $Path"
    return $false
}

function Initialize-DotnetEnvironment {
    $dotnetRoot = Split-Path -Parent $Dotnet
    if (-not $env:DOTNET_ROOT) {
        $env:DOTNET_ROOT = $dotnetRoot
    }
    if (-not $env:DOTNET_CLI_HOME) {
        $env:DOTNET_CLI_HOME = Join-Path $repoRoot "build\workflow\dotnet-home"
    }
    if (-not $env:NUGET_PACKAGES -and (Test-Path -LiteralPath "D:\work\maa-deps\nuget")) {
        $env:NUGET_PACKAGES = "D:\work\maa-deps\nuget"
    }
    $env:DOTNET_NOLOGO = "1"
    $env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = "1"
}

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$Activity
    )

    Write-Host "> $Activity" -ForegroundColor DarkGray
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Activity failed with exit code $LASTEXITCODE"
    }
}

function Invoke-PythonTests {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python environment not found: $Python"
    }

    Write-Step "Python tests: tools/auto-push"
    $oldPythonPath = $env:PYTHONPATH
    $oldPythonDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    $oldTemp = $env:TEMP
    $oldTmp = $env:TMP
    $pytestRoot = Join-Path $repoRoot "build\workflow\pytest"
    try {
        New-Item -ItemType Directory -Force -Path $pytestRoot | Out-Null
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $env:TEMP = $pytestRoot
        $env:TMP = $pytestRoot
        $env:PYTHONPATH = if ($oldPythonPath) {
            "$autoPushRoot$([IO.Path]::PathSeparator)$oldPythonPath"
        }
        else {
            $autoPushRoot
        }
        Push-Location $autoPushRoot
        try {
            Invoke-Native $Python @(
                "-m",
                "pytest",
                "tests",
                "--basetemp=$pytestRoot",
                "-p",
                "no:cacheprovider"
            ) "pytest"
        }
        finally {
            Pop-Location
        }
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
        $env:PYTHONDONTWRITEBYTECODE = $oldPythonDontWriteBytecode
        $env:TEMP = $oldTemp
        $env:TMP = $oldTmp
    }
}

function Invoke-DotnetTests {
    if (-not (Test-Path -LiteralPath $Dotnet -PathType Leaf)) {
        throw "dotnet SDK not found: $Dotnet"
    }
    Test-RequiredFile $testProject "LocalAI test project" | Out-Null
    if ($script:failures -gt 0) {
        throw "Test project is missing."
    }

    Initialize-DotnetEnvironment
    $testOutput = Join-Path $verifyRoot "test"
    New-Item -ItemType Directory -Force -Path $testOutput | Out-Null

    Write-Step "C# contract tests and WPF compile validation"
    $arguments = @(
        "build",
        $testProject,
        "-c", $Configuration,
        "-r", "win-x64",
        "-p:Platform=x64",
        "-p:PostBuildEvent=",
        "-p:NuGetAudit=false",
        "-p:RestoreIgnoreFailedSources=true",
        "-p:OutputPath=$testOutput",
        "-v:minimal"
    )
    if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $testProject) "obj\project.assets.json")) {
        $arguments += "--no-restore"
    }
    Invoke-Native $Dotnet $arguments "dotnet build tests"

    $testExecutable = Join-Path $testOutput "MaaWpfGui.LocalAi.Tests.exe"
    if (-not (Test-Path -LiteralPath $testExecutable -PathType Leaf)) {
        throw "Test executable not found after build: $testExecutable"
    }
    Invoke-Native $testExecutable @() "C# contract tests"
}

function Invoke-GuiBuild {
    if (-not (Test-Path -LiteralPath $Dotnet -PathType Leaf)) {
        throw "dotnet SDK not found: $Dotnet"
    }
    if (-not (Test-Path -LiteralPath $wpfProject -PathType Leaf)) {
        throw "WPF project not found: $wpfProject"
    }

    $resolvedOutput = if ($OutputPath) {
        if ([IO.Path]::IsPathRooted($OutputPath)) {
            [IO.Path]::GetFullPath($OutputPath)
        }
        else {
            [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
        }
    }
    else {
        Join-Path $repoRoot "build\bin\$Configuration"
    }

    if ($resolvedOutput -like (Join-Path $repoRoot "build\bin\*")) {
        $maaProcess = Get-Process MAA -ErrorAction SilentlyContinue
        if ($maaProcess) {
            throw "MAA.exe is running and can lock $resolvedOutput. Close it first, then rerun the build."
        }
    }

    Initialize-DotnetEnvironment
    New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
    Write-Step "Build MaaWpfGui ($Configuration)"
    $arguments = @(
        "build",
        $wpfProject,
        "-c", $Configuration,
        "-r", "win-x64",
        "-p:Platform=x64",
        "-p:OutputPath=$resolvedOutput",
        "-p:PostBuildEvent=",
        "-p:NuGetAudit=false",
        "-p:RestoreIgnoreFailedSources=true",
        "-v:minimal"
    )
    if (Test-Path -LiteralPath (Join-Path $repoRoot "src\MaaWpfGui\obj\project.assets.json")) {
        $arguments += "--no-restore"
    }
    Invoke-Native $Dotnet $arguments "dotnet build"
    Write-Result OK "GUI output: $resolvedOutput"
}

function Get-PackageLocalAiSettings {
    $configPath = Join-Path $PackageDir "config\gui.new.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $null
    }

    $text = Get-Content -Raw -LiteralPath $configPath -Encoding UTF8
    try {
        $config = $text | ConvertFrom-Json
        return $config.LocalAi
    }
    catch {
        $match = [regex]::Match($text, '(?s)"LocalAi"\s*:\s*(\{.*?\})')
        if ($match.Success) {
            try {
                return $match.Groups[1].Value | ConvertFrom-Json
            }
            catch {
                return $null
            }
        }

        return $null
    }
}

function Test-OllamaEndpoint {
    param([string]$Endpoint)

    $uri = $null
    if (-not [Uri]::TryCreate($Endpoint, [UriKind]::Absolute, [ref]$uri) -or $null -eq $uri) {
        Write-Result WARN "LocalAI endpoint is not a valid absolute URI: $Endpoint"
        return
    }

    if (-not $uri.IsLoopback) {
        Write-Result WARN "LocalAI endpoint is non-loopback: $Endpoint"
        return
    }

    try {
        $tagsUri = $uri.GetLeftPart([UriPartial]::Authority) + "/api/tags"
        $json = Invoke-RestMethod -Uri $tagsUri -Method Get -TimeoutSec 3
        Write-Result OK "Ollama API reachable: $tagsUri"

        $settings = Get-PackageLocalAiSettings
        if ($settings -and $settings.Model) {
            $models = @($json.models | ForEach-Object { $_.name })
            if ($models -contains $settings.Model) {
                Write-Result OK "Configured model is installed: $($settings.Model)"
            }
            else {
                Write-Result WARN "Configured model was not listed by Ollama: $($settings.Model)"
            }
        }
    }
    catch {
        Write-Result WARN "Ollama API is not reachable at ${Endpoint}: $($_.Exception.Message)"
    }
}

function Invoke-Doctor {
    Write-Step "Workspace"
    Write-Result OK "Repository: $repoRoot"
    if (Test-Path -LiteralPath (Join-Path $repoRoot ".git")) {
        & git -c "safe.directory=$repoRoot" -C $repoRoot status --short --branch
    }
    else {
        Write-Result WARN "Git metadata not found."
    }

    Write-Step "Toolchain"
    Test-RequiredFile $Dotnet "dotnet SDK" | Out-Null
    Test-RequiredFile $Python "Auto-Push Python" | Out-Null
    Test-RequiredFile $wpfProject "WPF project" | Out-Null
    Test-RequiredFile $testProject "LocalAI tests" | Out-Null
    $cmake = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmake) {
        Write-Result OK "CMake: $($cmake.Source)"
    }
    else {
        Write-Result WARN "CMake is not on PATH; C# checks work, full MaaCore builds need CMake/Visual Studio."
    }

    Write-Step "LocalAI package"
    Test-RequiredFile (Join-Path $PackageDir "MAA.exe") "Packaged MAA" | Out-Null
    Test-RequiredFile (Join-Path $PackageDir "MaaCore.dll") "Packaged MaaCore" | Out-Null
    Test-RequiredFile (Join-Path $PackageDir "config\gui.new.json") "Packaged config" | Out-Null

    $settings = Get-PackageLocalAiSettings
    if ($settings) {
        Write-Result OK "Endpoint: $($settings.Endpoint)"
        Write-Result OK "Model: $($settings.Model)"
        if ($settings.AllowRemoteEndpoint) {
            Write-Result WARN "Remote LocalAI endpoints are enabled in the package config."
        }
        else {
            Write-Result OK "Remote LocalAI endpoints are blocked by default."
        }
        if (-not $SkipEndpoint) {
            Test-OllamaEndpoint $settings.Endpoint
        }
    }
    else {
        Write-Result WARN "LocalAI settings could not be read from the package config."
    }

    Write-Step "Runtime"
    $processes = Get-Process MAA, MuMuVMMHeadless, MuMuNxDevice, MuMuNxMain, ollama -ErrorAction SilentlyContinue
    if ($processes) {
        $processes |
            Select-Object ProcessName, Id, PriorityClass, @{Name = "WorkingSetMB"; Expression = { [math]::Round($_.WorkingSet64 / 1MB) } } |
            Format-Table -AutoSize
        if (Get-Process MAA -ErrorAction SilentlyContinue) {
            Write-Result WARN "MAA is running; close it before building into build\bin\$Configuration."
        }
    }
    else {
        Write-Result OK "MAA, MuMu, and Ollama are not running."
    }

    $adbCandidates = @(
        (Join-Path $PackageDir "adb.exe"),
        "D:\Program Files\Netease\MuMu\nx_main\adb.exe"
    )
    $adb = $adbCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($adb) {
        Write-Result OK "ADB: $adb"
        $devices = & $adb devices 2>$null
        if (($devices | Select-Object -Skip 1 | Where-Object { $_ -match "\sdevice$" }).Count -gt 0) {
            Write-Result OK "ADB has a connected device."
        }
        else {
            Write-Result WARN "ADB is available but no ready device is connected."
        }
    }
    else {
        Write-Result WARN "ADB executable was not found."
    }

    Write-Step "Summary"
    Write-Host ("{0} failure(s), {1} warning(s)." -f $script:failures, $script:warnings)
    if ($script:failures -gt 0) {
        exit 1
    }
}

function Invoke-RunUi {
    $executable = Join-Path $repoRoot "build\bin\$Configuration\MAA.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Built executable not found: $executable. Run build-gui first."
    }
    if (Get-Process MAA -ErrorAction SilentlyContinue) {
        Write-Result WARN "MAA is already running; no new process was started."
        return
    }

    Write-Step "Launch MAA UI preview"
    $process = Start-Process -FilePath $executable -ArgumentList "--skip-core-init" -WorkingDirectory (Split-Path -Parent $executable) -PassThru
    Start-Sleep -Seconds 2
    try {
        $process.PriorityClass = "BelowNormal"
        Write-Result OK "Started MAA PID $($process.Id) at BelowNormal priority."
    }
    catch {
        Write-Result WARN "MAA started, but process priority could not be changed."
    }
}

function Invoke-DebugCli {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python environment not found: $Python"
    }

    $oldPythonPath = $env:PYTHONPATH
    $oldPythonUtf8 = $env:PYTHONUTF8
    $exitCode = 0
    try {
        $env:PYTHONUTF8 = "1"
        $env:PYTHONPATH = if ($oldPythonPath) {
            "$autoPushRoot$([IO.Path]::PathSeparator)$oldPythonPath"
        }
        else {
            $autoPushRoot
        }
        Push-Location $autoPushRoot
        try {
            & $Python -m autopush.debug_cli @CommandArguments
            $exitCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
        $env:PYTHONUTF8 = $oldPythonUtf8
    }
    if ($exitCode -ne 0) {
        exit $exitCode
    }
}

switch ($Command) {
    "doctor" { Invoke-Doctor }
    "debug" { Invoke-DebugCli }
    "test-python" { Invoke-PythonTests }
    "test-dotnet" { Invoke-DotnetTests }
    "build-gui" { Invoke-GuiBuild }
    "test" {
        if (-not $SkipPython) {
            Invoke-PythonTests
        }
        if (-not $SkipDotnet) {
            Invoke-DotnetTests
        }
    }
    "verify" {
        if (-not $SkipPython) {
            Invoke-PythonTests
        }
        if (-not $SkipDotnet) {
            Invoke-DotnetTests
        }
        $verifyOutput = Join-Path $verifyRoot "gui"
        $savedOutput = $OutputPath
        try {
            $OutputPath = $verifyOutput
            Invoke-GuiBuild
        }
        finally {
            $OutputPath = $savedOutput
        }
        Write-Step "Verification complete"
    }
    "run-ui" { Invoke-RunUi }
}
