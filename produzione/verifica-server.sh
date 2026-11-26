#!/bin/sh
# Fotografia del server PRIMA di installare Metodo v2 accanto alla versione già in produzione.
# Solo lettura: non installa, non modifica e non riavvia niente. Non stampa password (le credenziali
# negli indirizzi e nelle righe di cron vengono mascherate).
#
#   sudo sh produzione/verifica-server.sh [dominio-attuale] [dominio-di-prova]
#   es.: sudo sh produzione/verifica-server.sh metodo.esempio.it metodo2.esempio.it
#
# Il risultato va a schermo e in /tmp/verifica-metodo-<data>.txt: rileggilo prima di condividerlo.
set -u
DOMINIO=${1:-}
PROVA=${2:-}
USCITA="/tmp/verifica-metodo-$(date +%Y%m%d-%H%M).txt"

maschera() {  # credenziali negli URL e parametri tipo password=…
    sed -E 's#(://[^:/@ ]*):[^@ ]*@#\1:***@#g; s#((pass|password|pwd|secret|token|key)[A-Za-z_]*[=: ]+)[^ ;&"]+#\1***#Ig'
}
titolo() { printf '\n===== %s =====\n' "$1"; }
c() { command -v "$1" >/dev/null 2>&1; }

{
echo "Verifica del server per Metodo v2 — $(date '+%d/%m/%Y %H:%M')"
[ "$(id -u)" = 0 ] || echo "(senza root: alcune informazioni mancano; meglio con sudo)"

titolo "Sistema"
hostname
grep -E '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"'
uname -r
echo "CPU: $(nproc 2>/dev/null)"
free -h 2>/dev/null | sed -n '1,3p'
echo "Avvio: $(uptime -p 2>/dev/null)"

titolo "Dischi"
df -hT -x tmpfs -x devtmpfs -x overlay 2>/dev/null

titolo "Docker"
if c docker; then
    docker version --format 'Docker {{.Server.Version}}' 2>&1 | head -1
    docker compose version 2>&1 | head -1
    echo "Cartella dati: $(docker info --format '{{.DockerRootDir}}' 2>/dev/null)"
    echo "--- container"
    docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null
    echo "--- volumi"
    docker volume ls --format '{{.Name}}' 2>/dev/null
else
    echo "Docker NON installato"
fi

titolo "Porte in ascolto"
if c ss; then ss -tlnpH 2>/dev/null | awk '{print $4, $6}' | sort -u; else netstat -tlnp 2>/dev/null; fi

titolo "Web server"
if c nginx; then
    nginx -v 2>&1
    echo "--- siti (solo nomi, porte, destinazioni)"
    nginx -T 2>/dev/null | grep -E '^# configuration file|server_name|listen|proxy_pass|root |ssl_certificate |client_max_body_size' | maschera
fi
if c apache2ctl; then
    apache2ctl -v 2>/dev/null | head -1
    apache2ctl -S 2>/dev/null
    grep -rhE 'ServerName|ServerAlias|ProxyPass|DocumentRoot|SSLCertificateFile' /etc/apache2/sites-enabled 2>/dev/null | maschera
fi
c caddy && caddy version
c nginx || c apache2ctl || c caddy || echo "Nessun nginx / Apache / Caddy trovato"

titolo "Certificati HTTPS"
if c certbot; then certbot certificates 2>/dev/null | grep -E 'Certificate Name|Domains|Expiry'; else echo "certbot non installato"; fi
ls /etc/letsencrypt/live 2>/dev/null

titolo "Database PostgreSQL sul server"
c psql && psql --version
c pg_lsclusters && pg_lsclusters
ps -eo user,args 2>/dev/null | grep -E '[p]ostgres.*(-D|config_file)' | head -5
for f in /etc/postgresql/*/main/postgresql.conf; do
    [ -f "$f" ] && echo "$f: $(grep -E '^\s*(listen_addresses|port)\s*=' "$f" | tr '\n' ' ')"
done
for f in /etc/postgresql/*/main/pg_hba.conf; do
    [ -f "$f" ] && { echo "--- $f (regole attive)"; grep -vE '^\s*(#|$)' "$f"; }
done

titolo "Metodo attuale (dove si trova e come gira)"
for d in /var/www /opt /srv /home; do
    [ -d "$d" ] && find "$d" -maxdepth 2 -iname '*metodo*' 2>/dev/null
done
echo "--- servizi"
systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null | grep -iE 'metodo|gunicorn|uwsgi|php.*fpm|celery|supervisor' | awk '{print $1, $3, $4}'
echo "--- processi"
ps -eo user,args 2>/dev/null | grep -iE '[m]etodo|[g]unicorn|[u]wsgi|[c]elery' \
    | grep -vE 'verifica-(server|metodo)|shell-snapshots' | cut -c1-200 | maschera | head -15
echo "--- cron (righe con metodo)"
for u in root www-data $(ls /home 2>/dev/null); do
    crontab -l -u "$u" 2>/dev/null | grep -vE '^\s*#' | grep -i metodo | sed "s/^/[$u] /" | maschera
done
grep -rhi metodo /etc/cron.d 2>/dev/null | grep -vE '^\s*#' | maschera

titolo "Firewall"
c ufw && ufw status 2>/dev/null | head -20
c nft && nft list ruleset 2>/dev/null | grep -cE 'accept|drop' | sed 's/^/regole nftables: /'

titolo "DNS"
for d in $DOMINIO $PROVA; do
    printf '%s -> %s\n' "$d" "$(getent hosts "$d" | awk '{print $1}' | tr '\n' ' ')"
done
echo "IP pubblici del server: $(hostname -I 2>/dev/null)"

titolo "Porte libere per Metodo v2 (solo locale, dietro il proxy)"
for p in 8000 8010 8020 8080; do
    if ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$p\$"; then echo "$p occupata"; else echo "$p libera"; fi
done
} 2>&1 | tee "$USCITA"

echo
echo "Salvato in $USCITA (rileggilo prima di condividerlo: le password sono mascherate)."
