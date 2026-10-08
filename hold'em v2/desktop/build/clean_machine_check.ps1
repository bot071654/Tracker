<#
.SYNOPSIS
  Install-and-verify check for a CLEAN Windows machine. Needs nothing but the installer: no Python, no repository.

.DESCRIPTION
  Copy PokerHandTracker-Setup.exe and this script to the machine, then (PowerShell, no administrator rights needed):

    powershell -ExecutionPolicy Bypass -File clean_machine_check.ps1 -Installer .\PokerHandTracker-Setup.exe `
        -Email you@example.com -Password 'your password'            # an account on the production backend

  It installs the app for the current user, runs the packaged program's own self-test and backend check, starts the program and
  looks at its window, restarts it, then uninstalls, reinstalls and uninstalls again, writing every result to
  clean_machine_check.log next to the script. It never clicks anything, never touches a game, and submits no hands (the backend
  check is read-only). The password is used only as an environment variable for the checks and is never written to the log.

  A "clean machine" means: no Python (the script reports whether one is on PATH), no repository, no development libraries.

.PARAMETER Installer  Path to PokerHandTracker-Setup.exe.
.PARAMETER Email      Backend account (optional: without it the sign-in checks are skipped and reported as SKIPPED).
.PARAMETER Password   Its password.
.PARAMETER ApiUrl     Override the backend address (default: the address baked into the installer).
#>
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$Email = "",
    [string]$Password = "",
    [string]$ApiUrl = ""
)
$ErrorActionPreference = "Continue"
$Log = Join-Path $PSScriptRoot "clean_machine_check.log"
"" | Set-Content $Log
$script:Failures = 0
function Result($name, [bool]$ok, $detail = "") {
    $word = if ($ok) { "PASS" } else { $script:Failures++; "FAIL" }
    $line = "{0}  {1}  {2}" -f $word, $name, $detail
    Write-Host $line; Add-Content $Log $line
}
$script:CleanEnvironment = $true
function CleanCheck($name, [bool]$ok, $detail = "") { if (-not $ok) { $script:CleanEnvironment = $false }; Result $name $ok $detail }
function Skipped($name, $why) { $line = "SKIPPED  $name  $why"; Write-Host $line; Add-Content $Log $line }

$Installer = (Resolve-Path $Installer).Path
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\Poker Hand Tracker"
$Exe = Join-Path $InstallDir "PokerHandTracker.exe"
$UserData = Join-Path $env:LOCALAPPDATA "PokerHandTracker"
$Work = Join-Path $env:TEMP "phtcheck"
New-Item -ItemType Directory -Force $Work | Out-Null

function Windows-Of($processId) {
    Add-Type -ErrorAction SilentlyContinue @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class W { public delegate bool P(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] static extern bool EnumWindows(P p, IntPtr l);
 [DllImport("user32.dll")] static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
 public static List<KeyValuePair<IntPtr,string>> Of(int pid) { var r = new List<KeyValuePair<IntPtr,string>>();
  EnumWindows((h, l) => { int p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) { var s = new StringBuilder(256); GetWindowText(h, s, 256); r.Add(new KeyValuePair<IntPtr,string>(h, s.ToString())); } return true; }, IntPtr.Zero); return r; } }
"@
    return [W]::Of($processId)
}

Add-Content $Log ("Clean-machine check, {0}, Windows {1}, {2}" -f (Get-Date -Format s), [Environment]::OSVersion.Version, $env:COMPUTERNAME)
# Windows puts a "python.exe" App Execution Alias stub in WindowsApps on EVERY machine (it only opens the Store); that is not Python.
$py = Get-Command python, py -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*\WindowsApps\*" } | Select-Object -First 1
$pyDirs = @("$env:LOCALAPPDATA\Programs\Python", "$env:LOCALAPPDATA\Python", "C:\Python3*", "$env:ProgramFiles\Python3*") | Where-Object { Test-Path $_ }
$pyFound = if ($py) { $py.Source } elseif ($pyDirs) { $pyDirs -join ", " } else { "" }
CleanCheck "no Python installed (a clean machine)" (-not $pyFound) $(if ($pyFound) { "FOUND: $pyFound  <-- this is NOT a clean machine" } else { "none (the Store stub in WindowsApps is ignored)" })
$pip = Get-Command pip, pip3 -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*\WindowsApps\*" } | Select-Object -First 1
CleanCheck "no pip" (-not $pip) $(if ($pip) { "FOUND: " + $pip.Source } else { "none" })
CleanCheck "no virtual environment active" (-not $env:VIRTUAL_ENV) ""
$vscode = (Get-Command code -ErrorAction SilentlyContinue) -or (Test-Path "$env:LOCALAPPDATA\Programs\Microsoft VS Code") -or (Test-Path "$env:ProgramFiles\Microsoft VS Code")
CleanCheck "no VS Code" (-not $vscode) $(if ($vscode) { "FOUND  <-- this is NOT a clean machine" } else { "none" })
CleanCheck "no development repository next to the installer" (-not (Test-Path (Join-Path (Split-Path $Installer) "app.py"))) ""
CleanCheck "installer is not already installed" (-not (Test-Path $InstallDir)) $InstallDir
CleanCheck "no earlier application data" (-not (Test-Path $UserData)) $UserData
Result "installer file" $true ("{0:N1} MB, SHA-256 {1}" -f ((Get-Item $Installer).Length / 1MB), (Get-FileHash $Installer -Algorithm SHA256).Hash)
$sig = Get-AuthenticodeSignature $Installer
Add-Content $Log ("Installer signature: {0} ({1})" -f $sig.Status, $sig.SignerCertificate.Subject)

# 1. install
$p = Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
Result "1. installation (silent, per-user, no administrator rights)" ($p.ExitCode -eq 0 -and (Test-Path $Exe)) "exit $($p.ExitCode)"
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Poker Hand Tracker.lnk"
Result "   Start Menu shortcut" (Test-Path $menu) $menu

# 2. self-test of the installed program
$report = Join-Path $Work "selftest.json"
Remove-Item $report -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $Exe -ArgumentList "--selftest", "`"$report`"" -Wait -PassThru
$data = if (Test-Path $report) { Get-Content $report -Raw | ConvertFrom-Json } else { $null }
Result "2. packaged self-test (resources, recognition identical to baseline, no PostgreSQL driver, TLS roots)" ($p.ExitCode -eq 0 -and $data.ok) $(if ($data) { "{0}/{1} checks passed" -f @($data.results | Where-Object ok).Count, @($data.results).Count } else { "no report" })
if ($data) { $data.results | Where-Object { -not $_.ok } | ForEach-Object { Add-Content $Log ("   FAILED: {0}: {1}" -f $_.check, $_.detail) } }
Result "3. application data folders created" ((Test-Path (Join-Path $UserData "config")) -and (Test-Path (Join-Path $UserData "logs")) -and (Test-Path (Join-Path $UserData "recognition\templates"))) $UserData
Result "   nothing user-specific was written inside the install folder" (-not (Test-Path (Join-Path $InstallDir "config\config.json")) -and -not (Test-Path (Join-Path $InstallDir "logs"))) ""

# 3. backend (read-only)
if ($ApiUrl) { New-Item -ItemType Directory -Force (Join-Path $UserData "config") | Out-Null; (@{ api_url = $ApiUrl } | ConvertTo-Json) | Set-Content (Join-Path $UserData "config\app.json") }
if ($Email -and $Password) {
    $env:POKER_TRACKER_CHECK_EMAIL = $Email; $env:POKER_TRACKER_CHECK_PASSWORD = $Password
    $report = Join-Path $Work "backend1.json"
    $p = Start-Process -FilePath $Exe -ArgumentList "--backend-check", "`"$report`"" -Wait -PassThru
    Remove-Item Env:POKER_TRACKER_CHECK_EMAIL, Env:POKER_TRACKER_CHECK_PASSWORD
    $data = if (Test-Path $report) { Get-Content $report -Raw | ConvertFrom-Json } else { $null }
    Result "4. sign in, API connection, read from PostgreSQL through the backend" ($p.ExitCode -eq 0 -and $data.ok) $(if ($data) { ($data.steps | ForEach-Object { "{0}={1}" -f $_.step.Substring(0, [Math]::Min(22, $_.step.Length)), $(if ($_.ok) { "ok" } else { $_.detail }) }) -join "; " } else { "no report" })
    $raw = if (Test-Path $report) { Get-Content $report -Raw } else { "" }
    Result "   the report holds no password or token" (($raw -notmatch [regex]::Escape($Password)) -and ($raw -notmatch "access_token")) ""
    $report = Join-Path $Work "backend2.json"
    $p = Start-Process -FilePath $Exe -ArgumentList "--backend-check", "`"$report`"" -Wait -PassThru
    $data = if (Test-Path $report) { Get-Content $report -Raw | ConvertFrom-Json } else { $null }
    Result "5. a new process is still signed in (restart keeps the session)" ($p.ExitCode -eq 0 -and $data.ok) ""
} else { Skipped "4./5. sign-in and API checks" "no -Email/-Password given" }

# 4. launch the program, look at its window, restart it
function Launch-And-Look($label) {
    $proc = Start-Process -FilePath $Exe -PassThru
    Start-Sleep -Seconds 15
    $alive = -not $proc.HasExited
    $titles = if ($alive) { (Windows-Of $proc.Id | ForEach-Object { $_.Value }) -join " | " } else { "" }
    Result "$label starts and shows a window" ($alive -and $titles) "windows: $titles"
    if ($alive) {
        Windows-Of $proc.Id | ForEach-Object { [void][W]::PostMessage($_.Key, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) }   # WM_CLOSE, what the close button sends
        if (-not $proc.WaitForExit(20000)) { Stop-Process -Id $proc.Id -Force }
        Result "   closes cleanly on request" ($proc.ExitCode -eq 0) "exit code $($proc.ExitCode)"
    }
    return $titles
}
$t = Launch-And-Look "6. application launch"
if ($Email -and $Password) { Result "   already signed in (no sign-in window)" (-not ($t -like "Sign in*")) $t } else { Add-Content $Log "   (not signed in: a sign-in window is expected)" }
$t = Launch-And-Look "7. restart: second launch"
$logFile = Join-Path $UserData "logs\tracker.log"
$text = if (Test-Path $logFile) { Get-Content $logFile -Raw } else { "" }
Result "   log written with the version banner, no secrets" ($text -match "Application startup: Poker Hand Tracker" -and ((-not $Password) -or $text -notmatch [regex]::Escape($Password))) $logFile
Result "   no PostgreSQL driver in the install and none attempted" (-not (Get-ChildItem $InstallDir -Recurse -Directory -Filter "psycopg*" -ErrorAction SilentlyContinue) -and $text -notmatch "Cannot connect to PostgreSQL") ""

# 5. uninstall, reinstall, uninstall
$unins = Join-Path $InstallDir "unins000.exe"
$p = Start-Process -FilePath $unins -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Start-Sleep -Seconds 3
Result "8. uninstall (silent)" ($p.ExitCode -eq 0 -and -not (Test-Path $InstallDir) -and -not (Test-Path $menu)) "program folder and shortcut removed"
Result "   the user's data is kept by a silent uninstall" (Test-Path $UserData) $UserData
$p = Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
Result "9. reinstall" ($p.ExitCode -eq 0 -and (Test-Path $Exe)) "exit $($p.ExitCode)"
if ($Email -and $Password) {
    $report = Join-Path $Work "backend3.json"
    $p = Start-Process -FilePath $Exe -ArgumentList "--backend-check", "`"$report`"", "--sign-out" -Wait -PassThru
    $data = if (Test-Path $report) { Get-Content $report -Raw | ConvertFrom-Json } else { $null }
    Result "   after reinstall: settings and sign-in survived, API works, sign-out works" ($p.ExitCode -eq 0 -and $data.ok) ""
}
$t = Launch-And-Look "10. launch again after the reinstall"
$p = Start-Process -FilePath (Join-Path $InstallDir "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Start-Sleep -Seconds 3
Result "11. final uninstall" (-not (Test-Path $InstallDir)) ""
Add-Content $Log ("Your data folder was left in place: $UserData  (delete it to return the machine to its original state)")
Add-Content $Log ("TOTAL failures: $script:Failures")
$verdict = if (-not $script:CleanEnvironment) { "NOT A CLEAN MACHINE: these results are a development-machine proxy and are NOT the clean Windows test." }
           elseif ($script:Failures -eq 0) { "CLEAN WINDOWS TEST: PASS" } else { "CLEAN WINDOWS TEST: FAIL ($script:Failures failed check(s))" }
Add-Content $Log $verdict
Write-Host "`n$verdict"
Write-Host "Results: $Log   -   failures: $script:Failures"
exit $script:Failures
