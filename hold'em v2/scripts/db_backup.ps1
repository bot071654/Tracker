# Back up the poker_tracker database to backups\poker_tracker_<date>.dump
#
#   powershell -ExecutionPolicy Bypass -File scripts\db_backup.ps1
#
# Settings are read from .env. The password is passed to pg_dump through
# PGPASSWORD for the length of this command and is never written anywhere.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

if (-not (Test-Path $envFile)) {
    Write-Error "No .env at $envFile - copy .env.example and fill it in."
}

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

if (-not $dbHost -or -not $port -or -not $user -or -not $pass) {
    Write-Error "POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER and POSTGRES_PASSWORD must all be set in .env."
}

$backups = Join-Path $root "backups"
if (-not (Test-Path $backups)) { New-Item -ItemType Directory $backups | Out-Null }
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$out = Join-Path $backups "${name}_$stamp.dump"

Write-Output "Backing up ${user}@${dbHost}:${port}/${name}"

$env:PGPASSWORD = $pass
try {
    # -Fc is the custom format: compressed, and restorable with pg_restore.
    & pg_dump --host=$dbHost --port=$port --username=$user --dbname=$name `
              --format=custom --no-owner --no-privileges --file=$out
    if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump failed with exit code $LASTEXITCODE" }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

$size = "{0:N1} KB" -f ((Get-Item $out).Length / 1KB)
Write-Output "Wrote $out  ($size)"
