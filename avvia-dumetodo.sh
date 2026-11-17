#!/usr/bin/env bash
# Avvia DuMetodo su questo computer con Docker (Linux/macOS). Uso: ./avvia-dumetodo.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! docker info >/dev/null 2>&1; then
  echo "Docker non è avviato o non è installato: https://docs.docker.com/get-docker/" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "Creo la configurazione .env (password casuali)…"
  rnd() { python3 -c "import secrets;print(secrets.token_urlsafe($1))" 2>/dev/null || openssl rand -hex "$1"; }
  PGPASS=$(rnd 18)
  HOST=$(hostname)
  IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9.]+$' | paste -sd, - || true)
  ORIGINI="http://localhost:8000,http://127.0.0.1:8000,http://${HOST}:8000"
  for ip in ${IPS//,/ }; do ORIGINI="${ORIGINI},http://${ip}:8000"; done
  cat > .env <<CONF
# Generato da avvia-dumetodo.sh per l'uso in rete locale (http). Non va su git.
DJANGO_SECRET_KEY=$(rnd 48)
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,${HOST}${IPS:+,${IPS}}
DJANGO_CSRF_TRUSTED_ORIGINS=${ORIGINI}
DJANGO_HTTPS=false
SITE_URL=http://localhost:8000
POSTGRES_PASSWORD=${PGPASS}
DATABASE_URL=postgres://dumetodo:${PGPASS}@db:5432/dumetodo
CACHE_URL=rediscache://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/0
# Le email vengono solo scritte nel log finché non configuri un server SMTP (vedi .env.example).
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=dumetodo@localhost
CONF
fi

if ! grep -q '^POSTGRES_PASSWORD=.' .env; then
  echo "Il file .env esistente non contiene POSTGRES_PASSWORD: rinominalo (es. .env.vecchio) e rilancia lo script." >&2
  exit 1
fi

echo "Avvio DuMetodo (la prima volta può richiedere qualche minuto)…"
docker compose up -d --build

printf "Attendo che DuMetodo risponda"
for _ in $(seq 1 90); do
  if curl -fsS -o /dev/null http://localhost:8000/accesso/ 2>/dev/null; then echo " pronto."; break; fi
  printf "."; sleep 2
done
curl -fsS -o /dev/null http://localhost:8000/accesso/ || { echo; echo "DuMetodo non risponde: docker compose logs web" >&2; exit 1; }

docker compose exec -T web python manage.py crea_amministratore

CONF_CLIENT="${XDG_CONFIG_HOME:-$HOME/.config}/dumetodoclient/client.toml"
if [ ! -f "$CONF_CLIENT" ] || grep -q '^url = ""' "$CONF_CLIENT"; then
  mkdir -p "$(dirname "$CONF_CLIENT")"
  { echo 'url = "http://localhost:8000"'; grep -v '^url' "$CONF_CLIENT" 2>/dev/null || true; } > "$CONF_CLIENT.tmp" && mv "$CONF_CLIENT.tmp" "$CONF_CLIENT"
fi

echo
echo "DuMetodo è attivo: http://localhost:8000"
echo "Per fermarlo: docker compose down   (i dati restano)"
