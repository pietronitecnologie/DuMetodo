#!/bin/sh
# Analisi di sicurezza del server di Metodo v2. SOLO LETTURA: non installa, non modifica e non riavvia niente.
# Non legge password né chiavi (niente .env, docker inspect o journal completo) e maschera le credenziali.
#
# Dal PC (anche Windows, con ssh e scp già inclusi), come utente non root con sudo:
#   scp produzione/analisi-sicurezza.sh claude@SERVER:
#   ssh -t claude@SERVER "sudo sh analisi-sicurezza.sh /opt/metodo"
#   scp claude@SERVER:analisi-sicurezza-*.txt .
#
# Argomento: la cartella di Metodo v2 sul server (dove c'è compose.yaml); di base /opt/metodo.
set -u
METODO=${1:-/opt/metodo}
UTENTE=${SUDO_USER:-$(id -un)}
CASA=$(getent passwd "$UTENTE" | cut -d: -f6)
USCITA="${CASA:-/tmp}/analisi-sicurezza-$(date +%Y%m%d-%H%M).txt"

maschera() {
    sed -E 's#(://[^:/@ ]*):[^@ ]*@#\1:***@#g; /proxy_pass/!s#((pass|password|pwd|secret|token|key)[A-Za-z_]*[=: ]+)[^ ;&"]+#\1***#Ig'
}
titolo() { printf '\n===== %s =====\n' "$1"; }
c() { command -v "$1" >/dev/null 2>&1; }

{
echo "Analisi di sicurezza di $(hostname) — $(date '+%d/%m/%Y %H:%M')"
[ "$(id -u)" = 0 ] || echo "(senza root: molte informazioni mancano; lancia con sudo)"

titolo "Sistema"
grep -E '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"'
uname -r; uptime

titolo "Aggiornamenti in attesa"
apt list --upgradable 2>/dev/null | tail -n +2 | head -80
echo "Totale: $(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l)   di sicurezza: $(apt list --upgradable 2>/dev/null | grep -c -- '-security')"
titolo "Riavvio richiesto"
if [ -f /var/run/reboot-required ]; then cat /var/run/reboot-required.pkgs 2>/dev/null; else echo no; fi
titolo "Aggiornamenti automatici"
dpkg -l unattended-upgrades 2>/dev/null | tail -1; cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null

titolo "Porte in ascolto"
ss -tulpnH 2>/dev/null
titolo "Firewall"
if c ufw; then ufw status verbose; else echo "ufw non installato"; fi
iptables -S 2>/dev/null | head -60

titolo "SSH"
sshd -T 2>/dev/null | grep -Ei '^(port|permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|permitemptypasswords|x11forwarding|maxauthtries|allowusers|allowgroups|clientaliveinterval) '
echo "Accessi falliti (7 giorni): $(journalctl -u ssh -u sshd --since -7d --no-pager 2>/dev/null | grep -c 'Failed password')"
titolo "Utenti con shell e gruppi privilegiati"
awk -F: '$7 !~ /(nologin|false)$/ {print $1":"$3":"$7}' /etc/passwd
getent group sudo admin wheel docker
for d in /root /home/*; do f="$d/.ssh/authorized_keys"; [ -f "$f" ] && echo "$f: $(grep -c . "$f") chiavi"; done
ls -l /etc/sudoers.d/ 2>/dev/null
titolo "Ultimi accessi"
last -n 20 -a 2>/dev/null | head -25
titolo "fail2ban"
if c fail2ban-client; then fail2ban-client status; else echo "fail2ban non installato"; fi

titolo "nginx"
if c nginx; then
    nginx -v 2>&1; nginx -t 2>&1
    nginx -T 2>/dev/null | grep -Ei 'server_tokens|ssl_protocols|ssl_ciphers|add_header|listen|server_name|client_max_body_size|autoindex' | sed 's/^ *//' | sort | uniq -c | maschera
fi
titolo "Certificati"
for f in /etc/letsencrypt/live/*/cert.pem; do [ -f "$f" ] && echo "$f: $(openssl x509 -enddate -noout -in "$f")"; done
systemctl list-timers --all 2>/dev/null | grep -i certbot

titolo "Docker"
if c docker; then
    docker version --format 'Server {{.Server.Version}}' 2>/dev/null
    docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null
    cat /etc/docker/daemon.json 2>/dev/null || echo "nessun daemon.json"
fi
titolo "PostgreSQL"
ss -tlnpH 2>/dev/null | grep -E ':(5432|15432) ' || echo "PostgreSQL non in ascolto su porte di rete"
grep -Ev '^[[:space:]]*(#|$)' /etc/postgresql/*/main/pg_hba.conf 2>/dev/null
grep -E "^[[:space:]]*listen_addresses" /etc/postgresql/*/main/postgresql.conf 2>/dev/null
echo "Ponte socat metodo-ponte-db: $(systemctl is-active metodo-ponte-db 2>/dev/null || true)"

titolo "Servizi falliti"
systemctl --failed --no-legend 2>/dev/null
titolo "Permessi dei file sensibili"
stat -c '%A %U:%G %n' "$METODO/.env" /etc/shadow /etc/sudoers 2>/dev/null
titolo "File scrivibili da tutti"
find "$METODO" /etc -xdev -type f -perm -0002 2>/dev/null | head -30
titolo "Antivirus"
dpkg -l clamav 2>/dev/null | tail -1; systemctl is-active clamav-freshclam 2>/dev/null
titolo "Spazio su disco"
df -h -x tmpfs -x devtmpfs -x overlay 2>/dev/null
titolo "Diagnostica di MetodoConsole"
if c metodoconsole; then metodoconsole diagnostica 2>&1 | tail -60 | maschera; else echo "metodoconsole non installato"; fi
} 2>&1 | tee "$USCITA"

chown "$UTENTE" "$USCITA" 2>/dev/null; chmod 600 "$USCITA"
echo
echo "Rapporto salvato in $USCITA (rileggilo prima di condividerlo)."
