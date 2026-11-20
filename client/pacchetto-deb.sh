#!/bin/sh
# Crea il pacchetto Debian del client: dumetodo-client_X.Y.Z_<arch>.deb nella cartella indicata (predefinita: target/).
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
BIN=${CARGO_TARGET_DIR:-target}/release/dumetodo-client
[ -x "$BIN" ] || { echo "Manca $BIN: compila prima (cargo build --release)" >&2; exit 1; }

RADICE=$(mktemp -d)
trap 'rm -rf "$RADICE"' EXIT
install -D -m 755 "$BIN" "$RADICE/usr/bin/dumetodo-client"
install -D -m 644 pacchetto/dumetodo-client.desktop "$RADICE/usr/share/applications/dumetodo-client.desktop"
for lato in 16 24 32 48 64 256; do
    install -D -m 644 "../static/img/dumetodo-$lato.png" "$RADICE/usr/share/icons/hicolor/${lato}x$lato/apps/dumetodo-client.png"
done
install -D -m 644 /dev/null "$RADICE/usr/share/doc/dumetodo-client/copyright"
printf 'DuMetodo Client %s\nSoftware proprietario. Tutti i diritti riservati.\n' "$VERSIONE" > "$RADICE/usr/share/doc/dumetodo-client/copyright"

# Dipendenze calcolate dalle librerie usate davvero (dpkg-shlibdeps), altrimenti l'elenco noto.
DIPENDENZE="libwebkit2gtk-4.1-0, libgtk-3-0, libxdo3, libc6"
if command -v dpkg-shlibdeps >/dev/null; then
    LAVORO=$(mktemp -d)
    mkdir "$LAVORO/debian" && printf 'Source: dumetodo-client\n\nPackage: dumetodo-client\nArchitecture: any\n' > "$LAVORO/debian/control"
    CALCOLATE=$(cd "$LAVORO" && dpkg-shlibdeps -O "$RADICE/usr/bin/dumetodo-client" 2>/dev/null | sed -n 's/^shlibs:Depends=//p') || true
    rm -rf "$LAVORO"
    [ -n "$CALCOLATE" ] && DIPENDENZE=$CALCOLATE
fi
mkdir -p "$RADICE/DEBIAN"
cat > "$RADICE/DEBIAN/control" <<FINE
Package: dumetodo-client
Version: $VERSIONE
Architecture: $ARCH
Maintainer: DuMetodo <dumetodo@localhost>
Installed-Size: $(du -sk "$RADICE/usr" | cut -f1)
Depends: $DIPENDENZE
Recommends: xdg-utils
Section: misc
Priority: optional
Homepage: https://github.com/pietronitecnologie/DuMetodo
Description: Client desktop di DuMetodo
 Browser dedicato a DuMetodo: schede, menu del tasto destro, scorciatoie da
 tastiera, chat con notifiche. La grafica e le funzioni arrivano dal server.
FINE
mkdir -p "$USCITA"
PACCHETTO="$USCITA/dumetodo-client_${VERSIONE}_$ARCH.deb"
dpkg-deb --build --root-owner-group -Zxz "$RADICE" "$PACCHETTO" >/dev/null
echo "$PACCHETTO"
