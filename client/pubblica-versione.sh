#!/usr/bin/env bash
# Pubblica una nuova versione del client su GitHub. Uso: ./client/pubblica-versione.sh 0.3.0
set -euo pipefail
cd "$(dirname "$0")/.."
versione="${1:?indica la versione, es. 0.3.0}"
[[ "$versione" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Versione non valida: usa 0.3.0" >&2; exit 1; }
attuale=$(sed -n 's/^version = "\(.*\)"$/\1/p' client/Cargo.toml)
[ "$(printf '%s\n%s\n' "$attuale" "$versione" | sort -V | tail -1)" = "$versione" ] && [ "$versione" != "$attuale" ] \
  || { echo "La versione $versione non è più alta di $attuale" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Ci sono modifiche non salvate:" >&2; git status --short >&2; exit 1; }
git pull --ff-only
sed -i "s/^version = \".*\"$/version = \"$versione\"/" client/Cargo.toml
sed -i "/^name = \"dumetodo-client\"$/{n;s/^version = .*/version = \"$versione\"/}" client/Cargo.lock
git add client/Cargo.toml client/Cargo.lock && git commit -m "Client $versione" && git tag "client-v$versione"
git push && git push origin "client-v$versione"
echo "Pubblicazione avviata: tra qualche minuto in GitHub > Releases trovi 'DuMetodo Client $versione'."
