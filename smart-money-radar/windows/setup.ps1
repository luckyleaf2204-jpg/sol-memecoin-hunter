# Smart Money Radar - first time setup on Windows (creates/updates the .env file).
# Run through setup.bat. ASCII only on purpose (Windows PowerShell 5 reads files as ANSI).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$envFile = Join-Path $root ".env"
$example = Join-Path $root ".env.example"

function New-Secret([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return -join ($b | ForEach-Object { $_.ToString("x2") })
}
function Get-Value([string[]]$lines, [string]$key) {
    foreach ($l in $lines) { if ($l -match "^\s*$key=(.*)$") { return $Matches[1].Trim() } }
    return $null
}
function Set-Value([System.Collections.ArrayList]$lines, [string]$key, [string]$value) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$key=") { $lines[$i] = "$key=$value"; return }
    }
    [void]$lines.Add("$key=$value")
}

Write-Host ""
Write-Host "=== SMART MONEY RADAR - CAI DAT LAN DAU ===" -ForegroundColor Cyan
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Chua cai Docker Desktop. Tai tai https://www.docker.com/products/docker-desktop/ roi chay lai." -ForegroundColor Red
    exit 1
}
$isNew = -not (Test-Path $envFile)
if ($isNew) { Copy-Item $example $envFile; Write-Host "Da tao file .env tu .env.example" }
else { Write-Host "Da co file .env - chi bo sung gia tri con thieu (khong doi mat khau database)." }

$lines = New-Object System.Collections.ArrayList
[void]$lines.AddRange([string[]](Get-Content $envFile))

# secrets generated automatically - never change POSTGRES_PASSWORD after the first start
if (-not (Get-Value $lines "SESSION_SECRET")) { Set-Value $lines "SESSION_SECRET" (New-Secret 32) }
if (-not (Get-Value $lines "POSTGRES_PASSWORD")) { Set-Value $lines "POSTGRES_PASSWORD" (New-Secret 16) }
if (-not (Get-Value $lines "LOCAL_PORT")) { Set-Value $lines "LOCAL_PORT" "8080" }
if (-not (Get-Value $lines "LOCAL_BIND")) { Set-Value $lines "LOCAL_BIND" "127.0.0.1" }
$port = Get-Value $lines "LOCAL_PORT"
Set-Value $lines "DOMAIN" "localhost"
Set-Value $lines "PUBLIC_URL" "http://localhost:$port"
if ((Get-Value $lines "BACKUP_AT_UTC") -eq "19:30" -or -not (Get-Value $lines "BACKUP_AT_UTC")) {
    Set-Value $lines "BACKUP_AT_UTC" "05:00"   # 12:00 trua gio Viet Nam (may thuong dang bat)
}

function Ask([string]$key, [string]$prompt, [bool]$required) {
    $cur = Get-Value $lines $key
    if ($cur) { Write-Host "$key : da co (Enter de giu nguyen)" }
    $v = Read-Host $prompt
    if ($v) { Set-Value $lines $key $v.Trim() }
    elseif ($required -and -not $cur) { Write-Host "  -> bat buoc, hay nhap." -ForegroundColor Yellow; Ask $key $prompt $required }
}
Write-Host ""
Ask "DASHBOARD_PASSWORD" "Mat khau dang nhap dashboard (tu dat, dai >= 10 ky tu)" $true
Ask "HELIUS_API_KEY" "Helius API key (dang dung goi tra phi)" $false
$plan = Read-Host "Goi Helius cua ban cho phep bao nhieu request/giay? (Enter = giu nguyen, vi du 50)"
if ($plan) { Set-Value $lines "HELIUS_RPS" $plan.Trim() }
Ask "TELEGRAM_BOT_TOKEN" "Telegram bot token (Enter de bo qua)" $false
Ask "TELEGRAM_CHAT_ID" "Telegram chat id (Enter de bo qua, gui /start cho bot de lay)" $false

# UTF-8 WITHOUT BOM (a BOM would corrupt the first variable name)
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envFile, (($lines -join "`n") + "`n"), $enc)
Write-Host ""
Write-Host "Da luu .env. Buoc tiep theo: bam dup windows\start.bat" -ForegroundColor Green
