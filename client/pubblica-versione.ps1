# Pubblica una nuova versione del client su GitHub (Release con l'exe allegato).
# Uso, nella cartella di DuMetodo:  powershell -ExecutionPolicy Bypass -File .\client\pubblica-versione.ps1 0.3.0
# Non serve Rust: compila GitHub. Dopo qualche minuto la trovi in GitHub > Releases.
param([Parameter(Mandatory = $true)][string]$Versione)
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path $PSScriptRoot -Parent)

if ($Versione -notmatch '^\d+\.\d+\.\d+$') { Write-Host "Versione non valida: usa il formato 0.3.0" -ForegroundColor Red; exit 1 }
$cargo = "client\Cargo.toml"
$attuale = (Select-String -Path $cargo -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$vecchia = [version]$attuale; $nuova = [version]$Versione
if ($nuova -le $vecchia) { Write-Host "La versione $Versione non è più alta di quella attuale ($attuale)." -ForegroundColor Red; exit 1 }
$modifiche = git status --porcelain
if ($modifiche) {
    Write-Host "Ci sono modifiche non salvate: prima fai commit (o annullale). File interessati:" -ForegroundColor Red
    $modifiche | ForEach-Object { Write-Host "  $_" }
    exit 1
}

git pull --ff-only
# Scrittura senza BOM: Cargo.toml deve restare UTF-8 semplice.
$righe = (Get-Content $cargo) -replace '^version = ".+"$', "version = `"$Versione`""
[System.IO.File]::WriteAllLines((Resolve-Path $cargo), $righe, (New-Object System.Text.UTF8Encoding $false))
git add $cargo
git commit -m "Client $Versione"
git tag "client-v$Versione"
git push
git push origin "client-v$Versione"
Write-Host ""
Write-Host "Pubblicazione avviata: tra qualche minuto in GitHub > Releases trovi 'DuMetodo Client $Versione'" -ForegroundColor Green
Write-Host "con dumetodo-client-$Versione.exe. Poi caricalo in DuMetodo > Amministrazione > Aggiornamenti del client."
