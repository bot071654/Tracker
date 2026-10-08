<#
.SYNOPSIS
  Build the Windows x64 package and installer for Poker Hand Tracker.

.DESCRIPTION
  Run on a Windows x64 machine from anywhere:   powershell -ExecutionPolicy Bypass -File desktop\build\build_windows.ps1 -Python "C:\Path\to\python.exe" -ApiUrl https://your-api.example.com

  Steps: build venv -> install pinned requirements + PyInstaller -> tests -> stage defaults + build info -> PyInstaller ->
  verify the bundle (content scan + the packaged exe's own self-test) -> Inno Setup installer -> SHA-256 file.
  Any failing step stops the build.

.PARAMETER Python       Python 3.14 x64 (the version the app was tested on). Default: "py -3.14" if present, else "python".
.PARAMETER ApiUrl       https address of the production backend baked into this build (public, not a secret). Optional.
.PARAMETER BuildNumber  Default: UTC timestamp yyyyMMddHHmm.
.PARAMETER SkipTests    Skip the Python test run (not recommended for a release).
.PARAMETER SkipInstaller  Stop after the verified bundle (use when Inno Setup is not installed).
.PARAMETER SignToolArgs   Optional: arguments for signtool, e.g. '/sha1 <thumbprint> /fd sha256 /tr http://timestamp.digicert.com /td sha256'.
#>
param(
    [string]$Python = "",
    [string]$ApiUrl = "",
    [string]$BuildNumber = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmm"),
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$SignToolArgs = ""
)
$ErrorActionPreference = "Stop"
function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $exe $($arguments -join ' ')" }
}

$Build = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $Build "..\..")).Path
$Venv = Join-Path $Build ".venv-win"
$Dist = Join-Path $Build "dist"
$Work = Join-Path $Build "work"

Step "Python"
if (-not $Python) { $Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" } }
$pyArgs = @(); if ($Python -eq "py") { $pyArgs = @("-3.14") }
Run $Python ($pyArgs + @("--version"))
$arch = & $Python @pyArgs -c "import platform,struct; print(platform.machine(), struct.calcsize('P')*8)"
Write-Host "Interpreter: $arch"
if ($arch -notmatch "AMD64 64") { throw "A Windows x64 (AMD64) Python is required; got: $arch" }

Step "Build environment ($Venv)"
if (-not (Test-Path $Venv)) { Run $Python ($pyArgs + @("-m", "venv", $Venv)) }
$VPy = Join-Path $Venv "Scripts\python.exe"
Run $VPy @("-m", "pip", "install", "--upgrade", "pip")
Run $VPy @("-m", "pip", "install", "-r", (Join-Path $Build "requirements-build.txt"))
Run $VPy @("-m", "pip", "freeze")   # recorded in the build log: the exact versions used

if (-not $SkipTests) {
    Step "Tests (desktop + recognition baseline)"
    Push-Location $Root
    try {
        Run $VPy @("-m", "pip", "install", "pytest", "pillow", "psycopg[binary]", "python-dotenv", "fastapi", "uvicorn[standard]")   # test-only; never part of the bundle
        Run $VPy @("-m", "pytest", "tests/test_desktop_paths.py", "tests/test_api_client.py", "tests/test_desktop_bootstrap.py", "tests/test_all_52_cards.py", "tests/test_cards.py", "-q", "-p", "no:cacheprovider")
        Run $VPy @("-m", "pip", "uninstall", "-y", "psycopg", "psycopg-binary", "python-dotenv", "fastapi", "uvicorn", "pillow", "pytest")
    } finally { Pop-Location }
}

Step "Stage defaults and build info"
$stageArgs = @((Join-Path $Build "prepare_stage.py"), "--build-number", $BuildNumber)
if ($ApiUrl) { $stageArgs += @("--api-url", $ApiUrl) }      # an empty value would be dropped by PowerShell and leave the flag without one
Run $VPy $stageArgs

Step "PyInstaller"
$env:BUILD_NUMBER = $BuildNumber
Push-Location $Root
try {
    Run $VPy @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", $Dist, "--workpath", $Work, (Join-Path $Build "PokerHandTracker.spec"))
} finally { Pop-Location }
$Bundle = Join-Path $Dist "PokerHandTracker"

Step "Verify the bundle (content scan + the packaged exe's own self-test)"
Run $VPy @((Join-Path $Build "verify_bundle.py"), $Bundle)

if ($SignToolArgs) {
    Step "Sign the executable"
    Run "signtool" (@("sign") + ($SignToolArgs -split " ") + @((Join-Path $Bundle "PokerHandTracker.exe")))
}

if ($SkipInstaller) { Write-Host "`nBundle ready (installer skipped): $Bundle"; exit 0 }

Step "Installer (Inno Setup)"
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) was not found. Install it from https://jrsoftware.org/isinfo.php or pass -SkipInstaller." }
$version = & $VPy -c "from desktop import version; print(version.VERSION)"
Push-Location $Root
try {
    Run $iscc @("/Qp", "/DSourceDir=$Bundle", "/DAppVersion=$version", "/DBuildNumber=$BuildNumber", "/O$Dist", (Join-Path $Build "installer\PokerHandTracker.iss"))
} finally { Pop-Location }
$Setup = Join-Path $Dist "PokerHandTracker-Setup.exe"
if ($SignToolArgs) { Run "signtool" (@("sign") + ($SignToolArgs -split " ") + @($Setup)) }
$hash = (Get-FileHash $Setup -Algorithm SHA256).Hash
"$hash  PokerHandTracker-Setup.exe" | Set-Content (Join-Path $Dist "PokerHandTracker-Setup.exe.sha256") -Encoding ascii
Write-Host "`nInstaller: $Setup`nSHA-256:   $hash"
