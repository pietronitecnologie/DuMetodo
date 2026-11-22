#!/usr/bin/env bash
# Preleva i dati da Metodo in DuMetodo (Docker su questo computer) attraverso un tunnel SSH.
#
#   ./preleva-da-metodo.sh                          chiede i dati, verifica e avvia (o riprende) il prelievo
#   ./preleva-da-metodo.sh --max-kb 256 --pausa 1   le opzioni vengono passate a preleva_da_metodo
#   ./preleva-da-metodo.sh --ricomincia ordini righe consegne
#
# Le risposte (tranne le password) vengono ricordate per la volta successiva in
# ~/.config/dumetodo/prelievo-metodo.conf. La password del database non finisce mai sulla riga di comando
# né su disco; quella SSH la chiede ssh stesso (o si usa la chiave SSH).
set -euo pipefail

CONF_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/dumetodo/prelievo-metodo.conf"
SOCKET=""

# ---------------------------------------------------------------------------------------------- utilità
urlencode() {  # codifica per l'URL (es. @ -> %40), byte per byte
  local LC_ALL=C s="$1" o="" c i h
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) o+="$c" ;;
      *) printf -v h '%%%02X' "'$c"; o+="$h" ;;
    esac
  done
  printf '%s' "$o"
}

chiedi() {  # chiedi VARIABILE "domanda" [predefinito]
  local var="$1" domanda="$2" predefinito="${3:-}" risposta
  read -r -p "$domanda${predefinito:+ [$predefinito]}: " risposta
  printf -v "$var" '%s' "${risposta:-$predefinito}"
}

si_no() {  # si_no "domanda" (predefinito sì)
  local r
  read -r -p "$1 [S/n] " r
  [[ ! "$r" =~ ^[nN] ]]
}

porta_libera() {
  ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

chiudi_tunnel() {
  if [[ -n "$SOCKET" && -S "$SOCKET" ]]; then
    ssh -S "$SOCKET" -O exit "$SSH_DEST" >/dev/null 2>&1 || true
    echo "Tunnel SSH chiuso."
  fi
}

apri_tunnel() {
  SOCKET="$(mktemp -u "${TMPDIR:-/tmp}/dumetodo-tunnel.XXXXXX")"
  echo "Apro il tunnel SSH verso $SSH_DEST (porta $SSH_PORTA)…"
  # Il tunnel ascolta solo sull'indirizzo della rete Docker: il container lo raggiunge come
  # host.docker.internal, gli altri computer della rete no.
  ssh -f -N -M -S "$SOCKET" -p "$SSH_PORTA" \
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -C \
    -L "$ASCOLTO:$TUNNEL_PORTA:$DB_HOST:$DB_PORTA" "$SSH_DEST"
}

# -------------------------------------------------------------------------------------------- principale
main() {
  cd "$(dirname "$0")"
  if ! docker compose ps --status running --services 2>/dev/null | grep -qx web; then
    echo "DuMetodo non è in esecuzione: avvialo con ./avvia-dumetodo.sh" >&2
    exit 1
  fi
  if ! command -v ssh >/dev/null; then
    echo "Manca il client SSH: sudo apt install openssh-client" >&2
    exit 1
  fi

  SSH_HOST="" SSH_UTENTE="root" SSH_PORTA="22" DB_HOST="localhost" DB_PORTA="5432"
  DB_NOME="metodo" DB_UTENTE="dumetodo_lettura" TUNNEL_PORTA="15432"
  # shellcheck disable=SC1090
  [[ -f "$CONF_FILE" ]] && source "$CONF_FILE"

  echo "== Server di Metodo (accesso SSH) =="
  chiedi SSH_HOST "Indirizzo del server" "$SSH_HOST"
  chiedi SSH_UTENTE "Utente SSH" "$SSH_UTENTE"
  chiedi SSH_PORTA "Porta SSH" "$SSH_PORTA"
  echo "== Database di Metodo (visto dal server) =="
  chiedi DB_HOST "Host del database" "$DB_HOST"
  chiedi DB_PORTA "Porta del database" "$DB_PORTA"
  chiedi DB_NOME "Nome del database" "$DB_NOME"
  chiedi DB_UTENTE "Utente del database" "$DB_UTENTE"
  read -r -s -p "Password del database: " DB_PASSWORD
  echo
  chiedi TUNNEL_PORTA "Porta locale del tunnel" "$TUNNEL_PORTA"

  if [[ -z "$SSH_HOST" || -z "$DB_PASSWORD" ]]; then
    echo "Indirizzo del server e password del database sono obbligatori." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$CONF_FILE")"
  umask 077
  cat >"$CONF_FILE" <<CONF
# Ultimi dati usati da preleva-da-metodo.sh (le password non vengono salvate).
SSH_HOST='$SSH_HOST'
SSH_UTENTE='$SSH_UTENTE'
SSH_PORTA='$SSH_PORTA'
DB_HOST='$DB_HOST'
DB_PORTA='$DB_PORTA'
DB_NOME='$DB_NOME'
DB_UTENTE='$DB_UTENTE'
TUNNEL_PORTA='$TUNNEL_PORTA'
CONF

  SSH_DEST="$SSH_UTENTE@$SSH_HOST"
  ASCOLTO="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
  if [[ -z "$ASCOLTO" ]] || ! ip -o addr 2>/dev/null | grep -q " $ASCOLTO/"; then
    ASCOLTO="0.0.0.0"
    echo "Attenzione: rete Docker non trovata, il tunnel ascolta su tutte le interfacce (porta $TUNNEL_PORTA)."
  fi
  if ! porta_libera "$TUNNEL_PORTA"; then
    echo "La porta $TUNNEL_PORTA è già in uso (un tunnel ancora aperto?): scegline un'altra." >&2
    exit 1
  fi

  trap chiudi_tunnel EXIT
  trap 'echo; echo "Interrotto: rilancia lo script per riprendere da dove era arrivato."; exit 130' INT
  apri_tunnel

  METODO_DB_URL="postgres://$(urlencode "$DB_UTENTE"):$(urlencode "$DB_PASSWORD")@host.docker.internal:$TUNNEL_PORTA/$(urlencode "$DB_NOME")"
  export METODO_DB_URL
  unset DB_PASSWORD
  local manage=(docker compose exec -e METODO_DB_URL web python manage.py preleva_da_metodo)

  echo
  echo "== Verifica =="
  if ! "${manage[@]}" --verifica --tentativi 2; then
    echo "Verifica non riuscita: controlla i dati inseriti (utente, password, nome del database)." >&2
    exit 1
  fi
  echo
  si_no "Avviare (o riprendere) il prelievo?" || exit 0

  while true; do
    if "${manage[@]}" --log /app/media/prelievo-metodo.log "$@"; then
      echo "Prelievo completato. Gli avvisi sono nel file prelievo-metodo.log dei file caricati di DuMetodo."
      exit 0
    fi
    echo "Il prelievo si è fermato con un errore (il lavoro fatto è salvato)." >&2
    si_no "Riaprire il tunnel e riprendere?" || exit 1
    chiudi_tunnel
    apri_tunnel
  done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
