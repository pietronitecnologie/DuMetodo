"""Area «docker» (qualunque progetto, non solo Metodo v2): demone, container non sani o in riavvio continuo,
container fermati con errore, spazio recuperabile."""

from __future__ import annotations

import json
import re

from .base import ATTENZIONE, ERRORE, OK, SERVE_ROOT, Contesto, Esito, esegui, presente

G = "docker"


def presente_area(ctx: Contesto) -> bool:
    return presente("docker")


def leggi_ps(testo: str) -> list[dict[str, str]]:
    out = []
    for riga in testo.splitlines():
        try:
            out.append(json.loads(riga))
        except ValueError:
            continue
    return out


def gb(testo: str) -> float:
    m = re.match(r"([\d.]+)\s*([kKMGT]?B)", testo or "")
    if not m:
        return 0.0
    return float(m.group(1)) * {"B": 1e-9, "kB": 1e-6, "KB": 1e-6, "MB": 1e-3, "GB": 1, "TB": 1000}[m.group(2)]


def controlli(ctx: Contesto) -> list[Esito]:
    r = esegui(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if not r.ok:
        if "permission denied" in r.errore.lower():
            return [Esito("Docker", OK, SERVE_ROOT, G)]
        return [Esito("Docker", ERRORE, "il demone non risponde: sudo systemctl status docker", G)]
    e = [Esito("Docker", OK, f"versione {r.testo.strip()}", G)]
    container = leggi_ps(esegui(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=30).testo)
    malati = [c["Names"] for c in container if "(unhealthy)" in c.get("Status", "")]
    in_riavvio = [c["Names"] for c in container if c.get("State") == "restarting"]
    falliti = [
        c["Names"]
        for c in container
        if c.get("State") == "exited"
        and not re.search(r"Exited \(0\)", c.get("Status", ""))
        and "-run-" not in c["Names"]
    ]
    attivi = sum(c.get("State") == "running" for c in container)
    e.append(Esito("Container in esecuzione", OK, f"{attivi} su {len(container)}", G))
    if malati or in_riavvio:
        e.append(
            Esito(
                "Container con problemi",
                ERRORE,
                ", ".join(
                    [*(f"{n} (non sano)" for n in malati), *(f"{n} (si riavvia di continuo)" for n in in_riavvio)]
                ),
                G,
            )
        )
    if falliti:
        e.append(Esito("Container fermati con errore", ATTENZIONE, ", ".join(falliti[:8]) + " — docker logs NOME", G))
    df = leggi_ps(esegui(["docker", "system", "df", "--format", "{{json .}}"], timeout=60).testo)
    recuperabile = sum(gb(d.get("Reclaimable", "").split(" ")[0]) for d in df)
    if recuperabile > 10:
        e.append(
            Esito(
                "Spazio di Docker",
                ATTENZIONE,
                f"{recuperabile:.0f} GB recuperabili (immagini e cache non usate): docker system prune",
                G,
            )
        )
    return e
