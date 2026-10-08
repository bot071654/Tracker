<#
.SYNOPSIS
  Assemble the Windows distribution folder: the installer, its checksum and the user guide - nothing else.

.DESCRIPTION
  Run after build_windows.ps1:   powershell -ExecutionPolicy Bypass -File desktop\build\make_release_folder.ps1
  Checks that dist\PokerHandTracker-Setup.exe still matches the checksum the build recorded, copies it, writes SHA256SUMS.txt,
  renders desktop\docs\INSTALL-WINDOWS.md (version and checksum filled in) and re-verifies the copy.
  Output: desktop\build\release\PokerHandTracker-<version>-windows-x64\   (ignored by git)
#>
$ErrorActionPreference = "Stop"
$Build = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $Build "..\..")).Path
$Setup = Join-Path $Build "dist\PokerHandTracker-Setup.exe"
$Recorded = Join-Path $Build "dist\PokerHandTracker-Setup.exe.sha256"
if (-not (Test-Path $Setup)) { throw "Build the installer first (build_windows.ps1): $Setup is missing." }

$version = [regex]::Match((Get-Content (Join-Path $Root "desktop\version.py") -Raw), 'VERSION\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $version) { throw "Could not read VERSION from desktop\version.py" }
$hash = (Get-FileHash $Setup -Algorithm SHA256).Hash
if (Test-Path $Recorded) {
    $expected = ((Get-Content $Recorded -TotalCount 1) -split "\s+")[0]
    if ($expected -ne $hash) { throw "The installer does not match the checksum recorded by the build ($expected vs $hash)." }
}

$Out = Join-Path $Build "release\PokerHandTracker-$version-windows-x64"
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out | Out-Null
Copy-Item $Setup (Join-Path $Out "PokerHandTracker-Setup.exe")
[System.IO.File]::WriteAllText((Join-Path $Out "SHA256SUMS.txt"), "$hash  PokerHandTracker-Setup.exe`n", (New-Object System.Text.ASCIIEncoding))   # LF only: sha256sum / shasum -c reject CRLF
$guide = (Get-Content (Join-Path $Root "desktop\docs\INSTALL-WINDOWS.md") -Raw -Encoding utf8).Replace("@VERSION@", $version).Replace("@SHA256@", $hash)
[System.IO.File]::WriteAllText((Join-Path $Out "INSTALL-WINDOWS.md"), $guide, (New-Object System.Text.UTF8Encoding($false)))

$copy = (Get-FileHash (Join-Path $Out "PokerHandTracker-Setup.exe") -Algorithm SHA256).Hash
if ($copy -ne $hash) { throw "The copied installer differs from the built one." }
Get-ChildItem $Out | Select-Object Name, Length | Format-Table -AutoSize
Write-Host "Release folder: $Out`nSHA-256: $hash"
