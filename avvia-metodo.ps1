# Avvia Metodo v2 su questo PC Windows con Docker Desktop.
# Uso (PowerShell, nella cartella di Metodo v2):  powershell -ExecutionPolicy Bypass -File .\avvia-metodo.ps1
# Con dati di prova (solo su un database vuoto):  ... -File .\avvia-metodo.ps1 -DatiDiProva
param([switch]$DatiDiProva)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop non è avviato o non è installato: https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}

function Casuale([int]$n) {
    $b = New-Object byte[] $n
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return ([Convert]::ToBase64String($b) -replace '[+/=]', 'x')
}

if (-not (Test-Path .env)) {
    Write-Host "Creo la configurazione .env (password casuali)..."
    $pg = Casuale 18
    $nome = $env:COMPUTERNAME
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
             ForEach-Object { $_.IPAddress })
    $hosts = @('localhost', '127.0.0.1', $nome) + $ips
    $origini = ($hosts | ForEach-Object { "http://$($_):8000" }) -join ','
    $righe = @(
        "# Generato da avvia-metodo.ps1 per l'uso in rete locale (http). Non va su git.",
        "DJANGO_SECRET_KEY=$(Casuale 48)",
        "DJANGO_ALLOWED_HOSTS=$($hosts -join ',')",
        "DJANGO_CSRF_TRUSTED_ORIGINS=$origini",
        "DJANGO_HTTPS=false",
        "SITE_URL=http://localhost:8000",
        "POSTGRES_PASSWORD=$pg",
        "DATABASE_URL=postgres://metodo:$pg@db:5432/metodo",
        "CACHE_URL=rediscache://redis:6379/1",
        "CELERY_BROKER_URL=redis://redis:6379/0",
        "# Le email vengono solo scritte nel log finché non configuri un server SMTP (vedi .env.example).",
        "EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend",
        "DEFAULT_FROM_EMAIL=metodo@localhost"
    )
    [System.IO.File]::WriteAllLines("$PSScriptRoot\.env", $righe)
}

if (-not (Select-String -Path .env -Pattern '^POSTGRES_PASSWORD=.' -Quiet)) {
    Write-Host "Il file .env esistente non contiene POSTGRES_PASSWORD: rinominalo (es. .env.vecchio) e rilancia lo script." -ForegroundColor Red
    exit 1
}

# Versione del codice: mostrata anche in fondo a ogni pagina di Metodo v2.
# (con $ErrorActionPreference=Stop, Windows PowerShell trasforma l'output di errore di git in eccezione)
$versione = $null
try { $versione = (git rev-parse --short HEAD 2>$null) } catch { }
if ($versione) {
    $env:METODO_VERSIONE = $versione
    Write-Host "Versione del codice: $versione"
    $remota = $null
    try { git fetch --quiet origin 2>$null; $remota = (git rev-parse --short origin/main 2>$null) } catch { }
    if ($remota -and $remota -ne $versione) {
        Write-Host "È disponibile una versione più recente ($remota): esegui 'git pull' e rilancia lo script." -ForegroundColor Yellow
    }
} else {
    Write-Host "Questa cartella non è una copia git di Metodo v2: non si aggiorna con 'git pull'." -ForegroundColor Yellow
}

Write-Host "Avvio Metodo v2 (la prima volta può richiedere qualche minuto)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Write-Host "Avvio non riuscito." -ForegroundColor Red; exit 1 }

Write-Host -NoNewline "Attendo che Metodo v2 risponda"
$pronto = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8000/accesso/" -TimeoutSec 3 | Out-Null
        $pronto = $true; break
    } catch { Write-Host -NoNewline "."; Start-Sleep -Seconds 2 }
}
if (-not $pronto) { Write-Host ""; Write-Host "Metodo v2 non risponde: docker compose logs web" -ForegroundColor Red; exit 1 }
Write-Host " pronto."

docker compose exec -T web python manage.py crea_amministratore
if ($DatiDiProva) { docker compose exec -T web python manage.py carica_dati_demo }

# Collega il client desktop a questo server (solo se non ha già un indirizzo).
$confClient = Join-Path $env:APPDATA "Metodo\Metodo Client\config\client.toml"
$attuale = if (Test-Path $confClient) { Get-Content $confClient -Raw } else { "" }
if (-not $attuale -or $attuale -match '(?m)^url = ""') {
    New-Item -ItemType Directory -Force -Path (Split-Path $confClient) | Out-Null
    $resto = ($attuale -split "`r?`n" | Where-Object { $_ -notmatch '^url\s*=' }) -join "`r`n"
    [System.IO.File]::WriteAllText($confClient, "url = `"http://localhost:8000`"`r`n$resto")
}

Write-Host ""
Write-Host "Metodo v2 è attivo: http://localhost:8000" -ForegroundColor Green
Write-Host "Dagli altri PC della rete: http://$($env:COMPUTERNAME):8000"
Write-Host "Per fermarlo: docker compose down   (i dati restano)"
$client = @(".\metodo-client.exe", ".\client\target\release\metodo-client.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($client) { Start-Process $client } else { Start-Process "http://localhost:8000" }
