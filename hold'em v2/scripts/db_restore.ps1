# Restore a dump made by scripts\db_backup.ps1
#
#   powershell -ExecutionPolicy Bypass -File scripts\db_restore.ps1 backups\poker_tracker_2026-09-22.dump
#   ... -Force      # allow restoring over a non-empty table
#
# Refuses by default if poker_hands already has rows, because restoring over a
# live history is how a history gets lost. Settings come from .env; the
# password is never written anywhere.

param(
    [Parameter(Mandatory = $true, Position = 0)] [string] $Dump,
    [switch] $Force
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

if (-not (Test-Path $Dump))    { Write-Error "No such dump: $Dump" }
if (-not (Test-Path $envFile)) { Write-Error "No .env at $envFile - copy .env.example and fill it in." }

function Get-Setting($name) {
    $line = Select-String -Path $envFile -Pattern "^$name=" | Select-Object -Last 1
    if ($null -eq $line) { return "" }
    return $line.Line.Substring($name.Length + 1).Trim()
}

$dbHost = Get-Setting "POSTGRES_HOST"
$port   = Get-Setting "POSTGRES_PORT"
$name   = Get-Setting "POSTGRES_DATABASE"
$user   = Get-Setting "POSTGRES_USER"
$pass   = Get-Setting "POSTGRES_PASSWORD"

Write-Output "Restoring into ${user}@${dbHost}:${port}/${name}"

$env:PGPASSWORD = $pass
try {
    # A missing table counts as empty.
    $rows = & psql --host=$dbHost --port=$port --username=$user --dbname=$name `
                   -tAc "SELECT coalesce((SELECT count(*) FROM poker_hands), 0)"
    if ($LASTEXITCODE -ne 0) { $rows = "0" }
    $rows = "$rows".Trim()

    if ($rows -ne "0" -and -not $Force) {
        Write-Output ""
        Write-Output "poker_hands already holds $rows row(s). Refusing to restore over it."
        Write-Output "Back up first:  powershell -File scripts\db_backup.ps1"
        Write-Output "Then re-run with -Force"
        exit 1
    }

    & pg_restore --host=$dbHost --port=$port --username=$user --dbname=$name `
                 --no-owner --no-privileges --if-exists --clean $Dump
    if ($LASTEXITCODE -ne 0) { Write-Error "pg_restore failed with exit code $LASTEXITCODE" }

    $after = & psql --host=$dbHost --port=$port --username=$user --dbname=$name `
                    -tAc "SELECT count(*) FROM poker_hands"
    Write-Output "Restored. poker_hands now holds $("$after".Trim()) row(s)."
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
