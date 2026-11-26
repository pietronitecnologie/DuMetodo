#!/bin/sh
# Crea il file .env di produzione di Metodo v2 con chiavi e password casuali generate qui, sul server.
# Le chiavi non vengono mostrate né scritte altrove. Non sovrascrive mai un .env esistente.
#
#   sh produzione/genera-env.sh                (nella cartella di Metodo v2, es. /opt/metodo)
set -eu
cd "$(dirname "$0")/.."
if [ -f .env ]; then
    echo ".env esiste già: non lo tocco. Per rifarlo, spostalo prima (mv .env .env.vecchio)." >&2
    exit 1
fi
command -v python3 >/dev/null || { echo "Serve python3" >&2; exit 1; }

chiedi() {  # chiedi "domanda" "predefinito" → risposta su stdout
    printf '%s [%s]: ' "$1" "$2" >&2
    read -r r || r=""
    echo "${r:-$2}"
}
casuale() { python3 -c "import secrets; print(secrets.token_urlsafe($1).replace('-', 'x').replace('_', 'y'))"; }

DOMINIO=$(chiedi "Dominio di Metodo v2 (es. metodo2.esempio.it)" "")
[ -n "$DOMINIO" ] || { echo "Il dominio è obbligatorio." >&2; exit 1; }
case "$DOMINIO" in *[!a-zA-Z0-9.-]*) echo "Dominio non valido: $DOMINIO" >&2; exit 1 ;; esac
PORTA=$(chiedi "Porta locale (deve essere libera; il proxy la raggiunge su 127.0.0.1)" "8010")
case "$PORTA" in ''|*[!0-9]*) echo "Porta non valida" >&2; exit 1 ;; esac
SMTP=$(chiedi "Server SMTP per le email in uscita (vuoto = lo imposti dopo)" "")
MITTENTE=$(chiedi "Indirizzo mittente delle email" "metodo@$DOMINIO")

PG=$(casuale 32)
umask 077
cat > .env <<EOF
# Metodo v2 in produzione — creato da produzione/genera-env.sh il $(date '+%d/%m/%Y %H:%M').
# Contiene chiavi e password: resta solo su questo server (permessi 600), non va su git né in email.
DJANGO_SECRET_KEY=$(casuale 50)
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=$DOMINIO
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMINIO
SITE_URL=https://$DOMINIO

# Solo il proxy (nginx/Apache) raggiunge Metodo v2, su questa porta locale.
METODO_INDIRIZZO=127.0.0.1
METODO_PORTA=$PORTA

POSTGRES_PASSWORD=$PG
DATABASE_URL=postgres://metodo:$PG@db:5432/metodo
CACHE_URL=rediscache://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/0

EMAIL_HOST=$SMTP
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=$MITTENTE

# Casella degli ordini: si imposta dall'app (Configurazione → Casella ordini) SOLO al momento del passaggio,
# quando il Metodo attuale smette di leggerla (vedi docs/messa-in-produzione.md).
ORDINI_IMAP_HOST=

# Solo per il prelievo dei dati dal Metodo attuale; toglilo a migrazione finita.
# METODO_DB_URL=postgres://metodo_lettura:PASSWORD@host.docker.internal:5432/metodo
EOF
chmod 600 .env
echo
echo "Creato .env per https://$DOMINIO (porta locale $PORTA), permessi 600."
[ -n "$SMTP" ] && echo "Ricorda: utente e password SMTP vanno scritti in .env (EMAIL_HOST_USER / EMAIL_HOST_PASSWORD)."
echo "Passo successivo: docker compose up -d --build"
