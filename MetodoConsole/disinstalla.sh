#!/bin/sh
# Disinstalla MetodoConsole (l'opposto di installa.sh). Metodo v2, i suoi container e i suoi dati NON vengono toccati.
#
#   sudo ./MetodoConsole/disinstalla.sh                  comando, controlli automatici, interfaccia, stato
#   sudo ./MetodoConsole/disinstalla.sh --configurazione anche /etc/metodoconsole e i log di MetodoConsole
#   sudo ./MetodoConsole/disinstalla.sh --backup         anche i BACKUP (chiede di scrivere ELIMINA)
#   sudo ./MetodoConsole/disinstalla.sh --si             senza domande (non vale per --backup)
#
# Restano sempre: la cartella del repository (MetodoConsole compreso: si reinstalla con installa.sh) e i
# programmi di sistema installati dall'interfaccia (ufw, ClamAV, fail2ban, unattended-upgrades).
set -eu

# Solo per i test: tutti i percorsi di sistema sotto questa cartella invece che sotto /.
R=${METODOCONSOLE_RADICE:-}

UNITA="metodoconsole-backup.timer metodoconsole-controllo.timer metodoconsole-backup.service metodoconsole-controllo.service"
COMANDO="$R/usr/local/bin/metodoconsole"
INTERFACCIA="$R/opt/metodoconsole"
STATO="$R/var/lib/metodoconsole"
CONFIGURAZIONE="$R/etc/metodoconsole"
LOG="$R/var/log/metodoconsole"
SYSTEMD="$R/etc/systemd/system"

CONF=0
BACKUP=0
SI=0
for a in "$@"; do
    case "$a" in
        --configurazione) CONF=1 ;;
        --backup) BACKUP=1 ;;
        --si) SI=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opzione sconosciuta: $a (vedi --help)" >&2; exit 2 ;;
    esac
done
[ -n "$R" ] || [ "$(id -u)" = 0 ] || { echo "Serve root: sudo $0 $*" >&2; exit 1; }

chiedi() {  # chiedi "domanda" → 0 se la risposta è s/S
    [ "$SI" = 1 ] && return 0
    printf '%s [s/N] ' "$1"
    read -r risposta || return 1
    case "$risposta" in s|S|si|sì|SI) return 0 ;; *) return 1 ;; esac
}

# Cartella dei backup dalla configurazione (sezione [backup], chiave cartella), letta prima di cancellarla.
CARTELLA_BACKUP="$R/var/backups/metodo"
if [ -f "$CONFIGURAZIONE/metodoconsole.conf" ]; then
    letta=$(awk -F= '
        /^\[/ { sezione = $0; next }
        sezione == "[backup]" && $1 ~ /^[ \t]*cartella[ \t]*$/ { gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit }
    ' "$CONFIGURAZIONE/metodoconsole.conf")
    [ -n "$letta" ] && CARTELLA_BACKUP="$R$letta"
fi

# Niente disinstallazione a metà di un backup o di un ripristino (stesso lucchetto di metodoconsole).
if [ -f "$STATO/operazione.lock" ] && command -v flock >/dev/null; then
    if ! flock -n "$STATO/operazione.lock" true; then
        echo "È in corso un backup o un ripristino di MetodoConsole: riprova quando è finito." >&2
        exit 1
    fi
fi

# --------------------------------------------------------------------------------------------- riepilogo
echo "Disinstallazione di MetodoConsole. Verranno rimossi:"
echo "  - il comando $COMANDO"
echo "  - i controlli automatici (backup notturno e diagnostica): $UNITA"
echo "  - l'interfaccia a schermo intero: $INTERFACCIA"
echo "  - lo stato interno: $STATO"
if [ "$CONF" = 1 ]; then
    echo "  - la configurazione $CONFIGURAZIONE e i log $LOG"
fi
if [ "$BACKUP" = 1 ]; then
    echo "  - TUTTI I BACKUP in $CARTELLA_BACKUP"
fi
echo "Restano:"
echo "  - Metodo v2: container, database e file caricati (continua a funzionare)"
[ "$CONF" = 1 ] || echo "  - la configurazione $CONFIGURAZIONE (per eliminarla: --configurazione)"
[ "$BACKUP" = 1 ] || echo "  - i backup in $CARTELLA_BACKUP (per eliminarli: --backup)"
echo "  - ufw, ClamAV, fail2ban e gli altri programmi di sistema installati dall'interfaccia"
echo
chiedi "Procedere?" || { echo "Annullato."; exit 0; }

# --------------------------------------------------------------------------------------------- systemd
if command -v systemctl >/dev/null; then
    # shellcheck disable=SC2086
    systemctl disable --now $UNITA 2>/dev/null || true
fi
for u in $UNITA; do
    rm -f "$SYSTEMD/$u"
done
if command -v systemctl >/dev/null; then
    systemctl daemon-reload 2>/dev/null || true
    # shellcheck disable=SC2086
    systemctl reset-failed $UNITA 2>/dev/null || true
fi
echo "Controlli automatici rimossi."

# --------------------------------------------------------------------------------------------- comando
# Solo se è davvero il comando di MetodoConsole (il collegamento creato da installa.sh).
if [ -L "$COMANDO" ]; then
    case "$(readlink "$COMANDO")" in
        */metodoconsole.py) rm -f "$COMANDO"; echo "Comando rimosso." ;;
        *) echo "Attenzione: $COMANDO non punta a MetodoConsole, lasciato com'è." >&2 ;;
    esac
elif [ -e "$COMANDO" ]; then
    echo "Attenzione: $COMANDO non è il collegamento di installa.sh, lasciato com'è." >&2
fi

# --------------------------------------------------------------------------------------------- cartelle
rm -rf "$INTERFACCIA" "$STATO"
echo "Interfaccia e stato rimossi."

if [ "$CONF" = 1 ]; then
    rm -rf "$CONFIGURAZIONE" "$LOG"
    echo "Configurazione e log rimossi."
fi

if [ "$BACKUP" = 1 ]; then
    # Mai una cartella di primo livello (/, /var, /home…): solo percorsi come /var/backups/metodo.
    case "${CARTELLA_BACKUP#"$R"}" in
        /*/*) ;;
        *) echo "Cartella dei backup sospetta ($CARTELLA_BACKUP): non la elimino." >&2; exit 1 ;;
    esac
    if [ ! -d "$CARTELLA_BACKUP" ]; then
        echo "Nessuna cartella dei backup in $CARTELLA_BACKUP."
    else
        n=$(find "$CARTELLA_BACKUP" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
        spazio=$(du -sh "$CARTELLA_BACKUP" 2>/dev/null | cut -f1)
        echo
        echo "ATTENZIONE: stai per eliminare $n backup ($spazio) in $CARTELLA_BACKUP."
        echo "Senza backup non si possono più recuperare i dati di Metodo v2 se qualcosa va storto."
        printf 'Scrivi ELIMINA per confermare: '
        read -r parola || parola=""
        if [ "$parola" = "ELIMINA" ]; then
            rm -rf "$CARTELLA_BACKUP"
            echo "Backup eliminati."
        else
            echo "Backup NON eliminati."
        fi
    fi
fi

echo
echo "MetodoConsole disinstallato. Metodo v2 continua a funzionare."
echo "Per reinstallarlo: sudo ./MetodoConsole/installa.sh"
