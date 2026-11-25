#!/bin/sh
# Crea il pacchetto Debian del client: metodo-client_X.Y.Z_<arch>.deb nella cartella indicata (predefinita: target/).
#   ./pacchetto-deb.sh [cartella di uscita] [--senza-compilare]
# Va eseguito sulla versione di Debian più vecchia da supportare (es. Debian 12): il programma
# richiede almeno la versione della libreria C con cui è stato compilato.
set -eu
cd "$(dirname "$0")"
USCITA=target
COMPILA=1
for a in "$@"; do
    case "$a" in
        --senza-compilare) COMPILA=0 ;;
        *) USCITA=$a ;;
    esac
done
VERSIONE=$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -1)
ARCH=$(dpkg --print-architecture)
[ "$COMPILA" = 1 ] && cargo build --release --locked
BIN=${CARGO_TARGET_DIR:-target}/release/metodo-client
[ -x "$BIN" ] || { echo "Manca $BIN: compila prima (cargo build --release)" >&2; exit 1; }

RADICE=$(mktemp -d)
trap 'rm -rf "$RADICE"' EXIT
install -D -m 755 "$BIN" "$RADICE/usr/bin/metodo-client"
install -D -m 644 pacchetto/metodo-client.desktop "$RADICE/usr/share/applications/metodo-client.desktop"
for lato in 16 24 32 48 64 256; do
    install -D -m 644 "../static/img/metodo-$lato.png" "$RADICE/usr/share/icons/hicolor/${lato}x$lato/apps/metodo-client.png"
done
install -D -m 644 /dev/null "$RADICE/usr/share/doc/metodo-client/copyright"
printf 'Metodo v2 Client %s\nSoftware proprietario. Tutti i diritti riservati.\n' "$VERSIONE" > "$RADICE/usr/share/doc/metodo-client/copyright"

# Dipendenze calcolate dalle librerie usate davvero (dpkg-shlibdeps), altrimenti l'elenco noto.
DIPENDENZE="libwebkit2gtk-4.1-0, libgtk-3-0, libxdo3, libc6"
if command -v dpkg-shlibdeps >/dev/null; then
    LAVORO=$(mktemp -d)
    mkdir "$LAVORO/debian" && printf 'Source: metodo-client\n\nPackage: metodo-client\nArchitecture: any\n' > "$LAVORO/debian/control"
    CALCOLATE=$(cd "$LAVORO" && dpkg-shlibdeps -O "$RADICE/usr/bin/metodo-client" 2>/dev/null | sed -n 's/^shlibs:Depends=//p') || true
    rm -rf "$LAVORO"
    [ -n "$CALCOLATE" ] && DIPENDENZE=$CALCOLATE
fi
mkdir -p "$RADICE/DEBIAN"
cat > "$RADICE/DEBIAN/control" <<FINE
Package: metodo-client
Version: $VERSIONE
Architecture: $ARCH
Maintainer: Metodo v2 <metodo@localhost>
Installed-Size: $(du -sk "$RADICE/usr" | cut -f1)
Depends: $DIPENDENZE
Recommends: xdg-utils
Replaces: dumetodo-client
Conflicts: dumetodo-client
Section: misc
Priority: optional
Homepage: https://github.com/pietronitecnologie/DuMetodo
Description: Client desktop di Metodo v2
 Browser dedicato a Metodo v2: schede, menu del tasto destro, scorciatoie da
 tastiera, chat con notifiche. La grafica e le funzioni arrivano dal server.
FINE
mkdir -p "$USCITA"
PACCHETTO="$USCITA/metodo-client_${VERSIONE}_$ARCH.deb"
dpkg-deb --build --root-owner-group -Zxz "$RADICE" "$PACCHETTO" >/dev/null
echo "$PACCHETTO"
