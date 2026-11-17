# Avvia DuMetodo su questo PC Windows SENZA Docker: serve solo Python 3.12 o più recente.
# Usa un database SQLite in .\dati: adatto a provarlo o a un piccolo ufficio su un solo PC server.
# Uso (PowerShell, nella cartella di DuMetodo):  powershell -ExecutionPolicy Bypass -File .\avvia-dumetodo-semplice.ps1
# Con dati di prova (solo su un database vuoto):  ... -File .\avvia-dumetodo-semplice.ps1 -DatiDiProva
param([switch]$DatiDiProva)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Versione del codice: mostrata anche in fondo a ogni pagina di DuMetodo.
# (con $ErrorActionPreference=Stop, Windows PowerShell trasforma l'output di errore di git in eccezione)
$versione = $null
try { $versione = (git rev-parse --short HEAD 2>$null) } catch { }
if ($versione) {
    $env:DUMETODO_VERSIONE = $versione
    Write-Host "Versione del codice: $versione"
    $remota = $null
    try { git fetch --quiet origin 2>$null; $remota = (git rev-parse --short origin/main 2>$null) } catch { }
    if ($remota -and $remota -ne $versione) {
        Write-Host "È disponibile una versione più recente ($remota): esegui 'git pull' e rilancia lo script." -ForegroundColor Yellow
    }
} else {
    Write-Host "Questa cartella non è una copia git di DuMetodo: non si aggiorna con 'git pull'." -ForegroundColor Yellow
}

# 1. Python
$python = $null
foreach ($c in @("py -3.13", "py -3.12", "python")) {
    $parti = @($c -split ' ')
    $exe = $parti[0]
    $extra = @($parti | Select-Object -Skip 1)
    try {
        $v = & $exe ($extra + @("-c", "import sys;print(sys.version_info>=(3,12))")) 2>$null
        if ($v -eq "True") { $python = $parti; break }
    } catch {}
}
if (-not $python) {
    Write-Host "Serve Python 3.12 o più recente. Installalo con:" -ForegroundColor Red
    Write-Host "  winget install Python.Python.3.12"
    Write-Host "(oppure da https://www.python.org/downloads/ spuntando 'Add python.exe to PATH'), poi rilancia lo script."
    exit 1
}

# 2. Ambiente Python e dipendenze (solo la prima volta o se cambiano)
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creo l'ambiente Python..."
    & $python[0] (@($python | Select-Object -Skip 1) + @("-m", "venv", ".venv"))
}
$firma = (Get-FileHash pyproject.toml).Hash
if (-not (Test-Path .venv\installato.txt) -or (Get-Content .venv\installato.txt) -ne $firma) {
    Write-Host "Installo le dipendenze (qualche minuto la prima volta)..."
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet ".[semplice]"
    if ($LASTEXITCODE -ne 0) { Write-Host "Installazione non riuscita." -ForegroundColor Red; exit 1 }
    Set-Content .venv\installato.txt $firma
}

# 3. Configurazione .env (segreti casuali, solo la prima volta)
function Casuale([int]$n) {
    $b = New-Object byte[] $n
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return ([Convert]::ToBase64String($b) -replace '[+/=]', 'x')
}
New-Item -ItemType Directory -Force -Path dati, dati\media | Out-Null
if (-not (Test-Path .env)) {
    Write-Host "Creo la configurazione .env..."
    $nome = $env:COMPUTERNAME
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
             ForEach-Object { $_.IPAddress })
    $hosts = @('localhost', '127.0.0.1', $nome) + $ips
    $origini = ($hosts | ForEach-Object { "http://$($_):8000" }) -join ','
    $db = ((Resolve-Path dati).Path -replace '\\', '/') + "/dumetodo.sqlite3"
    $righe = @(
        "# Generato da avvia-dumetodo-semplice.ps1 (installazione senza Docker). Non va su git.",
        "DJANGO_SECRET_KEY=$(Casuale 48)",
        "DJANGO_ALLOWED_HOSTS=$($hosts -join ',')",
        "DJANGO_CSRF_TRUSTED_ORIGINS=$origini",
        "DJANGO_HTTPS=false",
        "SITE_URL=http://localhost:8000",
        "DATABASE_URL=sqlite:///$db",
        "MEDIA_ROOT=$((Resolve-Path dati\media).Path)",
        "# Senza Redis/Celery i lavori (es. invio email) vengono eseguiti subito, nella richiesta.",
        "CELERY_TASK_ALWAYS_EAGER=true",
        "CELERY_BROKER_URL=memory://",
        "# Le email vengono solo scritte nel log finché non configuri un server SMTP (vedi .env.example).",
        "EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend",
        "DEFAULT_FROM_EMAIL=dumetodo@localhost"
    )
    [System.IO.File]::WriteAllLines("$PSScriptRoot\.env", $righe)
}

# 4. Database, file statici, ruoli e amministratore
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
& $py manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { Write-Host "Migrazione del database non riuscita." -ForegroundColor Red; exit 1 }
& $py manage.py collectstatic --noinput | Out-Null
& $py manage.py crea_ruoli
& $py manage.py crea_amministratore
if ($DatiDiProva) { & $py manage.py carica_dati_demo }

# 5. Collega il client desktop (solo se non ha già un indirizzo)
$confClient = Join-Path $env:APPDATA "DuMetodo\DuMetodo Client\config\client.toml"
$attuale = if (Test-Path $confClient) { Get-Content $confClient -Raw } else { "" }
if (-not $attuale -or $attuale -match '(?m)^url = ""') {
    New-Item -ItemType Directory -Force -Path (Split-Path $confClient) | Out-Null
    $resto = ($attuale -split "`r?`n" | Where-Object { $_ -notmatch '^url\s*=' }) -join "`r`n"
    [System.IO.File]::WriteAllText($confClient, "url = `"http://localhost:8000`"`r`n$resto")
}

# 6. Server (resta aperto in questa finestra)
Write-Host ""
Write-Host "DuMetodo è attivo: http://localhost:8000" -ForegroundColor Green
Write-Host "Dagli altri PC della rete: http://$($env:COMPUTERNAME):8000 (se Windows chiede il permesso del firewall, consenti)"
Write-Host "Per fermarlo chiudi questa finestra o premi Ctrl+C."
$client = @(".\dumetodo-client.exe", ".\client\target\release\dumetodo-client.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
$apri = if ($client) { (Resolve-Path $client).Path } else { "http://localhost:8000" }
Start-Job -ScriptBlock { param($a) Start-Sleep 3; Start-Process $a } -ArgumentList $apri | Out-Null
& $py -m waitress --listen=*:8000 --threads=8 config.wsgi:application
