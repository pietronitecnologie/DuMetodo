#!/bin/sh
# Installa MetodoConsole su Debian: comando «metodoconsole», configurazione e controlli automatici.
#   sudo ./MetodoConsole/installa.sh [cartella di Metodo v2]      (predefinita: la cartella di questo repository)
set -eu
[ "$(id -u)" = 0 ] || { echo "Serve root: sudo $0" >&2; exit 1; }
QUI=$(cd "$(dirname "$0")" && pwd)
METODO=${1:-$(dirname "$QUI")}
command -v docker >/dev/null || { echo "Docker non è installato (https://docs.docker.com/engine/install/debian/)" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Manca il plugin «docker compose»: apt install docker-compose-plugin" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Serve Python 3.11 o più recente" >&2; exit 1; }
command -v rsync >/dev/null || echo "Nota: rsync non è installato (serve solo per la copia remota dei backup: apt install rsync)."

install -d -m 755 /etc/metodoconsole /var/lib/metodoconsole
install -d -m 700 /var/backups/metodo
ln -sf "$QUI/metodoconsole.py" /usr/local/bin/metodoconsole
chmod 755 "$QUI/metodoconsole.py"
if [ ! -f /etc/metodoconsole/metodoconsole.conf ]; then
    sed "s|^cartella = /opt/metodo|cartella = $METODO|" "$QUI/metodoconsole.conf.example" > /etc/metodoconsole/metodoconsole.conf
    echo "Creata /etc/metodoconsole/metodoconsole.conf (Metodo v2 in $METODO): controllala."
else
    echo "/etc/metodoconsole/metodoconsole.conf esiste già: lasciata com'è."
fi
[ -f "$METODO/.env" ] && chmod 600 "$METODO/.env"

for u in "$QUI"/systemd/*; do install -m 644 "$u" /etc/systemd/system/; done
systemctl daemon-reload
systemctl enable --now metodoconsole-backup.timer metodoconsole-controllo.timer
echo
echo "MetodoConsole installato. Prova:  metodoconsole diagnostica"
systemctl list-timers 'metodoconsole-*' --no-pager
