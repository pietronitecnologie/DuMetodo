# Preleva i dati da Metodo in Metodo v2 (Docker Desktop su questo PC) attraverso un tunnel SSH.
#
# Uso (PowerShell, nella cartella di Metodo v2):
#   powershell -ExecutionPolicy Bypass -File .\preleva-da-metodo.ps1
#   powershell -ExecutionPolicy Bypass -File .\preleva-da-metodo.ps1 --max-kb 256 --pausa 1
#   powershell -ExecutionPolicy Bypass -File .\preleva-da-metodo.ps1 --ricomincia ordini righe consegne
#
# Le opzioni vengono passate a preleva_da_metodo. Le risposte (tranne le password) vengono ricordate in
# %APPDATA%\Metodo\prelievo-metodo.json. La password del database non finisce mai sulla riga di comando
# né su disco; quella SSH si scrive nella finestra di ssh (o si usa la chiave SSH).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$OpzioniPrelievo = $args

$ConfFile = Join-Path $env:APPDATA "Metodo\prelievo-metodo.json"
$script:Tunnel = $null

# ------------------------------------------------------------------------------------------------ utilità
function Chiedi([string]$Domanda, [string]$Predefinito) {
    $testo = if ($Predefinito) { "$Domanda [$Predefinito]" } else { $Domanda }
    $r = Read-Host $testo
    if ([string]::IsNullOrWhiteSpace($r)) { return $Predefinito }
    return $r.Trim()
}

function SiNo([string]$Domanda) {
    $r = Read-Host "$Domanda [S/n]"
    return -not ($r -match '^[nN]')
}

function Codifica([string]$Testo) {
    # Per l'indirizzo del database: @ -> %40, ! -> %21, ecc.
    return [Uri]::EscapeDataString($Testo).Replace("!", "%21").Replace("'", "%27").Replace("(", "%28").Replace(")", "%29").Replace("*", "%2A")
}

function PortaInAscolto([int]$Porta) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $attesa = $c.BeginConnect("127.0.0.1", $Porta, $null, $null)
        return ($attesa.AsyncWaitHandle.WaitOne(500) -and $c.Connected)
    } catch {
        return $false
    } finally {
        $c.Close()
    }
}

function ApriTunnel($c) {
    Write-Host "Apro il tunnel SSH verso $($c.SshUtente)@$($c.SshHost) (porta $($c.SshPorta))..."
    Write-Host "Se richiesto, scrivi la password SSH nella finestra che si apre e NON chiuderla finché il prelievo è in corso." -ForegroundColor Yellow
    $argomenti = @(
        "-N", "-C", "-p", $c.SshPorta,
        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4",
        # Solo su 127.0.0.1: Docker Desktop lo raggiunge come host.docker.internal, gli altri PC della rete no.
        "-L", "127.0.0.1:$($c.TunnelPorta):$($c.DbHost):$($c.DbPorta)",
        "$($c.SshUtente)@$($c.SshHost)"
    )
    $script:Tunnel = Start-Process -FilePath "ssh" -ArgumentList $argomenti -PassThru
    $fine = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $fine) {
        if ($script:Tunnel.HasExited) {
            throw "Il tunnel SSH si è chiuso (password errata o server non raggiungibile?)."
        }
        if (PortaInAscolto ([int]$c.TunnelPorta)) {
            Write-Host "Tunnel aperto." -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 500
    }
    ChiudiTunnel
    throw "Il tunnel SSH non si è aperto entro 3 minuti."
}

function ChiudiTunnel {
    if ($script:Tunnel -and -not $script:Tunnel.HasExited) {
        Stop-Process -Id $script:Tunnel.Id -Force -ErrorAction SilentlyContinue
        Write-Host "Tunnel SSH chiuso."
    }
    $script:Tunnel = $null
}

function Prelievo([string[]]$Extra) {
    # L'esito è in $LASTEXITCODE (0 = riuscito); l'output va direttamente a schermo, riga per riga.
    & docker compose exec -e METODO_DB_URL web python manage.py preleva_da_metodo @Extra
}

# ------------------------------------------------------------------------------------------- controlli
# In Windows PowerShell 5.1 i messaggi di errore dei programmi esterni rediretti diventerebbero eccezioni.
$ErrorActionPreference = "Continue"
$servizi = & docker compose ps --status running --services 2>$null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0 -or -not ($servizi -contains "web")) {
    Write-Host "Metodo v2 non è in esecuzione: avvialo con avvia-metodo.ps1 (e controlla che Docker Desktop sia aperto)." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host "Manca il client SSH di Windows: Impostazioni > App > Funzionalità facoltative > Client OpenSSH." -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------------------------- domande
$c = [ordered]@{
    SshHost = ""; SshUtente = "root"; SshPorta = "22"
    DbHost = "localhost"; DbPorta = "5432"; DbNome = "metodo"; DbUtente = "metodo_lettura"
    TunnelPorta = "15432"
}
if (Test-Path $ConfFile) {
    $salvato = Get-Content $ConfFile -Raw | ConvertFrom-Json
    foreach ($k in @($c.Keys)) { if ($salvato.$k) { $c[$k] = [string]$salvato.$k } }
}

Write-Host "== Server di Metodo (accesso SSH) ==" -ForegroundColor Cyan
$c.SshHost = Chiedi "Indirizzo del server" $c.SshHost
$c.SshUtente = Chiedi "Utente SSH" $c.SshUtente
$c.SshPorta = Chiedi "Porta SSH" $c.SshPorta
Write-Host "== Database di Metodo (visto dal server) ==" -ForegroundColor Cyan
$c.DbHost = Chiedi "Host del database" $c.DbHost
$c.DbPorta = Chiedi "Porta del database" $c.DbPorta
$c.DbNome = Chiedi "Nome del database" $c.DbNome
$c.DbUtente = Chiedi "Utente del database" $c.DbUtente
$segreta = Read-Host "Password del database" -AsSecureString
$c.TunnelPorta = Chiedi "Porta locale del tunnel" $c.TunnelPorta

$puntatore = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segreta)
try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($puntatore) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($puntatore) }

if (-not $c.SshHost -or -not $password) {
    Write-Host "Indirizzo del server e password del database sono obbligatori." -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path (Split-Path $ConfFile) | Out-Null
$c | ConvertTo-Json | Set-Content -Path $ConfFile -Encoding UTF8

if (PortaInAscolto ([int]$c.TunnelPorta)) {
    Write-Host "La porta $($c.TunnelPorta) è già in uso (un tunnel ancora aperto?): chiudilo o scegline un'altra." -ForegroundColor Red
    exit 1
}

# ------------------------------------------------------------------------------------------- prelievo
$env:METODO_DB_URL = "postgres://$(Codifica $c.DbUtente):$(Codifica $password)@host.docker.internal:$($c.TunnelPorta)/$(Codifica $c.DbNome)"
Remove-Variable password
$esito = 1
try {
    ApriTunnel $c
    Write-Host ""
    Write-Host "== Verifica ==" -ForegroundColor Cyan
    Prelievo @("--verifica", "--tentativi", "2")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Verifica non riuscita: controlla i dati inseriti (utente, password, nome del database)." -ForegroundColor Red
        exit 1
    }
    Write-Host ""
    if (-not (SiNo "Avviare (o riprendere) il prelievo?")) { $esito = 0; exit 0 }

    while ($true) {
        Prelievo (@("--log", "/app/media/prelievo-metodo.log") + $OpzioniPrelievo)
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Prelievo completato. Gli avvisi sono nel file prelievo-metodo.log dei file caricati di Metodo v2." -ForegroundColor Green
            $esito = 0
            break
        }
        Write-Host "Il prelievo si è fermato con un errore (il lavoro fatto è salvato)." -ForegroundColor Yellow
        if (-not (SiNo "Riaprire il tunnel e riprendere?")) { break }
        ChiudiTunnel
        ApriTunnel $c
    }
} finally {
    # Eseguito anche con Ctrl+C: il tunnel non resta aperto e l'indirizzo con la password sparisce.
    ChiudiTunnel
    Remove-Item Env:\METODO_DB_URL -ErrorAction SilentlyContinue
}
exit $esito
